"""
Run the labelled test_set through classify() and report accuracy.

  top-1  chosen hs6 == correct_hs6
  top-3  correct_hs6 appears among the top-N candidates

Also prints the decision path per example and asserts the hallucination guard
(every returned/candidate code exists in the taxonomy). This is the demo metric.

Usage:  python -m retrieval.evaluate
"""
import sqlite3

import config
from .pipeline import classify


def _load_testset():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT product_description, correct_hs6, category_tag "
            "FROM test_set ORDER BY category_tag, correct_hs6").fetchall()
    finally:
        conn.close()


def _valid_codes():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return {r[0] for r in conn.execute("SELECT hs6 FROM hs_taxonomy")}
    finally:
        conn.close()


def main():
    rows = _load_testset()
    valid = _valid_codes()
    n = len(rows)
    top1 = top3 = 0
    by_cat = {}
    guard_violations = 0

    print(f"Evaluating {n} test_set examples "
          f"(country=USA, LLM {'on' if _llm_on() else 'OFF — keyword+vector only'})\n")
    print(f"{'expected':9} {'got':9} {'t1':3} {'t3':3} {'path':8} desc")
    print("-" * 78)

    for r in rows:
        desc, gold, cat = r["product_description"], r["correct_hs6"], r["category_tag"]
        res = classify(desc)
        cand_codes = [c["hs6"] for c in res["candidates"]]

        # hallucination guard: nothing may reference a non-existent code
        for code in ([res["hs6"]] if res["hs6"] else []) + cand_codes:
            if code not in valid:
                guard_violations += 1

        is_t1 = res["hs6"] == gold
        is_t3 = gold in cand_codes
        top1 += is_t1
        top3 += is_t3
        c = by_cat.setdefault(cat, {"n": 0, "t1": 0, "t3": 0})
        c["n"] += 1
        c["t1"] += is_t1
        c["t3"] += is_t3

        print(f"{gold:9} {str(res['hs6'] or '-'):9} "
              f"{'✓' if is_t1 else '·':3} {'✓' if is_t3 else '·':3} "
              f"{res['path']:8} {desc[:38]}")

    print("-" * 78)
    print(f"\nOVERALL  top-1 {top1}/{n} = {100*top1/n:.0f}%   "
          f"top-3 {top3}/{n} = {100*top3/n:.0f}%")
    for cat, c in sorted(by_cat.items()):
        print(f"  {cat:11} top-1 {c['t1']}/{c['n']}   top-3 {c['t3']}/{c['n']}")
    print(f"\nhallucination-guard violations: {guard_violations} "
          f"(must be 0 — no invented codes)")


def _llm_on():
    from llm import backend_status
    s = backend_status()
    return bool(s["local_reachable"]) or s["fireworks_configured"]


if __name__ == "__main__":
    main()
