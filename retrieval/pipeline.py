"""
classify(query, country) — the whole retrieval chain in one call.

  Signal 1 keyword ─┐
  Signal 2 toc  ────┼─► Arbiter ─► (if classified) attach sourced card
  Signal 3 vector ──┘

Signal 2 (LLM) is optional: when absent it simply applies no scope filter.
The function NEVER raises on signal/LLM failure — it degrades.
"""
from . import signals
from . import arbiter
from .card import build_card


def classify(query, country="USA"):
    query = (query or "").strip()
    if not query:
        return {"query": query, "country": country, "decision": "out_of_scope",
                "hs6": None, "confidence": "low", "path": "empty",
                "signals_agreed": [], "candidates": [], "card": None,
                "reason": "Empty query."}

    # Signal 1 — keyword (cheap, always)
    keyword_hits = signals.keyword_search(query)

    # Signal 2 — scope routing (optional); narrows Signal 3 when available
    toc = signals.toc_route(query)
    scope_headings = toc["headings"] if (toc and toc["in_scope"] and toc["headings"]) else None

    # Signal 3 — vector (semantic), optionally scope-filtered
    vector_hits = signals.vector_search(query, scope_headings=scope_headings)

    result = arbiter.arbitrate(query, keyword_hits, vector_hits, toc)

    # Attach the sourced card only when we actually chose a code.
    card = None
    if result["decision"] == "classified" and result["hs6"]:
        card = build_card(result["hs6"], country)

    result.update({
        "query": query,
        "country": country,
        "card": card,
        "scope": {"routed": toc is not None, "headings": scope_headings,
                  "in_scope": (toc["in_scope"] if toc else None)},
    })
    return result
