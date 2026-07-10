"""
The three retrieval signals (README §4). Each is independent and fails SOFT:
a signal that can't run returns an empty result rather than raising, so the
pipeline degrades instead of crashing.

  Signal 1  keyword_search(q)          precise, cheap  (FTS5, LIKE fallback)
  Signal 2  toc_route(q)               scope filter    (LLM, optional)
  Signal 3  vector_search(q, scope)    semantic        (Chroma)

Contract: signals only NOMINATE candidates from the real taxonomy; none may
invent a code. The Arbiter decides among them.
"""
import re
import sqlite3

import config

# --- tokenization -----------------------------------------------------------
_STOP = {
    "a", "an", "the", "for", "of", "with", "and", "or", "in", "to", "on", "at",
    "by", "is", "are", "be", "used", "use", "type", "kind", "medical", "device",
    "apparatus", "product", "products", "item", "items",
}


def tokenize(text):
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in toks if t not in _STOP and len(t) > 1]


# ===========================================================================
# Signal 1 — SQL keyword match (FTS5 preferred, LIKE fallback)
# ===========================================================================
def _ensure_fts(conn):
    """Build a porter-stemmed FTS5 mirror of hs_taxonomy on this connection.
    Word-boundary + stemmed matching (so 'vaccine' hits 'vaccines' and 'ct'
    does NOT hit 'products'). Temp table: lives for the connection only.
    Returns True if FTS5 is usable, False to signal the LIKE fallback."""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.hs_fts "
            "USING fts5(hs6 UNINDEXED, description, keywords, "
            "tokenize='porter unicode61')"
        )
        # (re)populate — cheap at 85 rows
        conn.execute("DELETE FROM temp.hs_fts")
        conn.execute(
            "INSERT INTO temp.hs_fts (hs6, description, keywords) "
            "SELECT hs6, description, COALESCE(keywords, '') FROM hs_taxonomy"
        )
        return True
    except sqlite3.OperationalError:
        return False


def _keyword_fts(conn, tokens, k):
    # OR the tokens; quote each as a phrase so FTS5 syntax chars can't break it.
    match = " OR ".join(f'"{t}"' for t in tokens)
    rows = conn.execute(
        "SELECT hs6, bm25(hs_fts, 1.0, 1.0) AS rank "
        "FROM hs_fts WHERE hs_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, k),
    ).fetchall()
    # bm25: more negative = better. Normalise to a positive, monotet score.
    return [{"hs6": r[0], "score": round(-r[1], 4), "signal": "keyword"}
            for r in rows]


def _keyword_like(conn, tokens, k):
    # Fallback: count how many distinct tokens hit description|keywords.
    # NOTE: substring match — 'ct' hits 'products'. FTS5 path avoids this.
    scores = {}
    for t in tokens:
        like = f"%{t}%"
        for (hs6,) in conn.execute(
            "SELECT hs6 FROM hs_taxonomy WHERE description LIKE ? OR keywords LIKE ?",
            (like, like),
        ):
            scores[hs6] = scores.get(hs6, 0) + 1
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
    return [{"hs6": h, "score": float(s), "signal": "keyword"} for h, s in ranked]


def keyword_search(query, k=None):
    """Signal 1. Returns [{hs6, score, signal}] — may be empty, never wrong-but-
    confident. Empty query or no tokens => []."""
    k = k or config.KEYWORD_K
    tokens = tokenize(query)
    if not tokens:
        return []
    conn = sqlite3.connect(config.DB_PATH)
    try:
        if _ensure_fts(conn):
            hits = _keyword_fts(conn, tokens, k)
            if hits:
                return hits
        return _keyword_like(conn, tokens, k)
    finally:
        conn.close()


# ===========================================================================
# Signal 2 — TOC routing (scope resolution via LLM, OPTIONAL)
# ===========================================================================
_TOC_SCHEMA = {
    "type": "object",
    "properties": {
        "chapters": {"type": "array", "items": {
            "type": "string", "enum": sorted(config.ALL_CHAPTERS)}},
        "headings": {"type": "array", "items": {
            "type": "string", "enum": sorted(config.ALL_HEADINGS)}},
        "in_scope": {"type": "boolean"},
    },
    "required": ["chapters", "headings", "in_scope"],
    "additionalProperties": False,
}


def toc_route(query):
    """Signal 2. One cheap LLM call mapping the query onto the static scope map.
    Returns {chapters, headings, in_scope} or None if the LLM is unavailable
    (caller then applies NO scope filter — degrade, don't block)."""
    from llm import chat_json

    chapter_lines = "\n".join(f"  chapter {c}: {d}"
                              for c, d in config.SCOPE_CHAPTERS.items())
    heading_lines = "\n".join(f"  heading {h}: {d}"
                              for h, d in config.SCOPE_HEADINGS.items())
    sys = (
        "You are a customs classification scope router. The ONLY supported "
        "categories are:\n"
        f"{chapter_lines}\n{heading_lines}\n"
        "Given a product description, return the chapters/headings it plausibly "
        "belongs to (from the lists above only), and whether it is in scope at "
        "all. If it is clearly none of these (e.g. a laptop, a car), set "
        "in_scope=false and return empty lists. Return JSON only."
    )
    out = chat_json(
        [{"role": "system", "content": sys},
         {"role": "user", "content": query}],
        _TOC_SCHEMA,
    )
    if not out:
        return None
    # Defensive: drop anything not in the known sets (hallucination guard).
    out["chapters"] = [c for c in out.get("chapters", []) if c in config.ALL_CHAPTERS]
    out["headings"] = [h for h in out.get("headings", []) if h in config.ALL_HEADINGS]
    out["in_scope"] = bool(out.get("in_scope", True))
    return out


# ===========================================================================
# Signal 3 — Chroma vector search (semantic, always runs if Chroma present)
# ===========================================================================
_collection = None
_chroma_error = None


def _get_collection():
    global _collection, _chroma_error
    if _collection is not None or _chroma_error is not None:
        return _collection
    try:
        import chromadb
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        _collection = client.get_collection(config.CHROMA_COLLECTION)
    except Exception as e:                               # missing dep / no store
        _chroma_error = e
        _collection = None
    return _collection


def vector_search(query, scope_headings=None, k=None):
    """Signal 3. Returns [{hs6, similarity, signal}] sorted desc. Empty if
    Chroma is unavailable. `scope_headings` (from Signal 2) becomes a metadata
    filter when provided."""
    k = k or config.VECTOR_K
    coll = _get_collection()
    if coll is None:
        return []
    where = None
    if scope_headings:
        where = {"heading": {"$in": list(scope_headings)}}
    try:
        res = coll.query(query_texts=[query], n_results=k, where=where)
    except Exception:
        return []
    ids = (res.get("ids") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    out = []
    for hs6, dist in zip(ids, dists):
        sim = max(0.0, 1.0 - float(dist))               # cosine distance -> sim
        out.append({"hs6": hs6, "similarity": round(sim, 4), "signal": "vector"})
    return out
