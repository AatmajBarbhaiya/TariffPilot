"""
Arbiter (README §4): merge the signals' candidates and DECIDE, without ever
inventing a code.

  fast path   keyword & vector agree on one code, vector confident      -> high
  LLM path    LLM picks ONE of the nominated candidates                  -> medium
  no-pick     -> needs_review / out_of_scope, with a DISTINCT reason so the UI
              can tell them apart:
                scope           Signal 2 says out of scope
                no_match        nothing cleared the evidence threshold
                ambiguous       signals disagree AND the LLM is off
                llm_abstain     the LLM was asked and declined
                llm_unavailable the LLM was tried but gave no usable answer

Hallucination guard: on the LLM path the schema's `hs6` is an ENUM of exactly
the candidate codes (+ "abstain"), so locally a made-up code is grammatically
impossible; we ALSO re-validate the returned code against the candidate set so
the guard holds on any backend.
"""
import sqlite3

import config


# --- merge ------------------------------------------------------------------
def merge(keyword_hits, vector_hits):
    """Combine into one ranked candidate list, tracking which signals nominated
    each and carrying both scores. Rank: more signals > higher similarity >
    higher keyword score."""
    by_code = {}
    for h in keyword_hits:
        c = by_code.setdefault(h["hs6"], _blank(h["hs6"]))
        c["signals"].add("keyword")
        c["keyword_score"] = max(c["keyword_score"], h.get("score", 0.0))
    for h in vector_hits:
        c = by_code.setdefault(h["hs6"], _blank(h["hs6"]))
        c["signals"].add("vector")
        c["vector_similarity"] = max(c["vector_similarity"], h.get("similarity", 0.0))
    cands = list(by_code.values())
    cands.sort(key=lambda c: (len(c["signals"]), c["vector_similarity"],
                              c["keyword_score"]), reverse=True)
    return cands


def _blank(hs6):
    return {"hs6": hs6, "signals": set(), "keyword_score": 0.0,
            "vector_similarity": 0.0, "description": None}


def _attach_descriptions(cands):
    if not cands:
        return
    conn = sqlite3.connect(config.DB_PATH)
    try:
        rows = dict(conn.execute(
            "SELECT hs6, description FROM hs_taxonomy WHERE hs6 IN (%s)"
            % ",".join("?" * len(cands)),
            [c["hs6"] for c in cands],
        ).fetchall())
    finally:
        conn.close()
    for c in cands:
        c["description"] = rows.get(c["hs6"])


# --- decision ---------------------------------------------------------------
def arbitrate(query, keyword_hits, vector_hits, toc):
    cands = merge(keyword_hits, vector_hits)
    _attach_descriptions(cands)

    # Signal-2 guardrail: a confident out-of-scope verdict with NO lexical
    # evidence short-circuits (the "don't classify a laptop as medicine" rule).
    if toc is not None and not toc["in_scope"] and not keyword_hits:
        return _result("out_of_scope", None, "low", "scope", [], cands[:config.TOP_N],
                       "Signal 2 routed this outside the supported categories.")

    # A candidate qualifies if a keyword nominated it OR the vector is confident.
    qualified = [c for c in cands
                 if "keyword" in c["signals"]
                 or c["vector_similarity"] >= config.VECTOR_MIN_SIM]

    if not qualified:
        if toc and not toc["in_scope"]:
            return _result("out_of_scope", None, "low", "scope", [],
                           cands[:config.TOP_N],
                           "Signal 2 routed this outside the supported categories.")
        return _result("needs_review", None, "low", "no_match", [],
                       cands[:config.TOP_N],
                       "No candidate matched confidently — nothing cleared the "
                       "evidence threshold.")

    top_n = qualified[:config.TOP_N]

    # 1) Fast path — the STRONG agreement case only: keyword and vector both
    # rank the SAME code first, and the vector is confident. Anything more
    # ambiguous (top picks differ) is deferred to the LLM / abstain, so we never
    # stamp "high confidence" on a genuinely contested classification.
    if keyword_hits and vector_hits and keyword_hits[0]["hs6"] == vector_hits[0]["hs6"]:
        pick = next((c for c in qualified
                     if c["hs6"] == keyword_hits[0]["hs6"]), None)
        if pick and pick["vector_similarity"] >= config.FASTPATH_MIN_SIM:
            return _result("classified", pick["hs6"], "high", "fast",
                           sorted(pick["signals"]), top_n,
                           "Keyword and vector both rank this code first.")

    # 2) LLM path — let the model choose among the nominated candidates only.
    choice = _llm_arbitrate(query, qualified)
    if choice and choice != "abstain":
        pick = next((c for c in qualified if c["hs6"] == choice), None)
        if pick:                                        # validated ∈ candidates
            return _result("classified", pick["hs6"], "medium", "llm",
                           sorted(pick["signals"]), top_n,
                           "LLM selected from nominated candidates.")

    # 3) Abstain — surface top-N, and say WHICH kind of abstain this is so the
    # UI isn't a mystery: LLM declined vs LLM off vs LLM tried-but-no-answer.
    from llm import configured
    if choice == "abstain":
        path, reason = ("llm_abstain",
                        "The LLM reviewed the candidates and declined — no strong "
                        "fit among them.")
    elif not configured():
        path, reason = ("ambiguous",
                        "Signals disagree and the LLM is off — turn it on to "
                        "disambiguate these candidates.")
    else:
        path, reason = ("llm_unavailable",
                        "The LLM gave no usable answer (unreachable or invalid); "
                        "showing top candidates.")
    return _result("needs_review", None, "low", path, [], top_n, reason)


def _llm_arbitrate(query, qualified):
    """Ask the LLM to return one candidate hs6 or 'abstain'. Returns the code
    string, 'abstain', or None if the LLM is unavailable."""
    from llm import chat_json

    codes = [c["hs6"] for c in qualified]
    schema = {
        "type": "object",
        "properties": {
            "hs6": {"type": "string", "enum": codes + ["abstain"]},
            "reason": {"type": "string"},
        },
        "required": ["hs6", "reason"],
        "additionalProperties": False,
    }
    listing = "\n".join(f"  {c['hs6']}: {c['description']}" for c in qualified)
    sys = (
        "You are a customs HS-code classifier. Choose the SINGLE best HS6 code "
        "for the product from the candidate list below. You MUST pick one of the "
        "listed codes, or 'abstain' if none fits. Do not invent codes.\n"
        f"Candidates:\n{listing}\n"
        "Return JSON only."
    )
    out = chat_json(
        [{"role": "system", "content": sys},
         {"role": "user", "content": f"Product: {query}"}],
        schema,
    )
    if not out:
        return None
    hs6 = out.get("hs6")
    return hs6 if hs6 in codes or hs6 == "abstain" else None


def _result(decision, hs6, confidence, path, signals_agreed, candidates, reason):
    # Serialise candidates (sets -> sorted lists) for JSON friendliness.
    cand_out = [{
        "hs6": c["hs6"],
        "description": c["description"],
        "signals": sorted(c["signals"]),
        "keyword_score": c["keyword_score"],
        "vector_similarity": c["vector_similarity"],
    } for c in candidates]
    return {
        "decision": decision,           # classified | needs_review | out_of_scope
        "hs6": hs6,
        "confidence": confidence,       # high | medium | low
        "path": path,                   # classified: fast | llm
                                        # not:  scope | no_match | ambiguous
                                        #       | llm_abstain | llm_unavailable
        "signals_agreed": signals_agreed,
        "candidates": cand_out,
        "reason": reason,
    }
