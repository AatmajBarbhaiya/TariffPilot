"""
Run the JSON test set through classify() and report accuracy.

Test cases live in tests/test.json — edit that file to add or fix cases (it is
the single source of truth; the test data no longer lives in the database).

  top-1  chosen hs6 == correct_hs6
  top-3  correct_hs6 appears among the candidates (or was the final pick)
  source who produced the answer: keyword+vector | vLLM (local) | fireworks

Run from the PROJECT ROOT (Tariffpilot_rag_db/):
    python -m tests.evaluate               # all cases
    python -m tests.evaluate 10            # a random batch of 10
    python -m tests.evaluate 10 --seed 42  # reproducible random batch
"""
import argparse
import json
import random
import sqlite3
from pathlib import Path

import config
from retrieval.pipeline import classify

TEST_JSON = Path(__file__).resolve().parent / "test.json"

SRC_LABEL = {"local": "vLLM", "fireworks": "fireworks",
             "keyword+vector": "kw+vec", "llm": "llm"}


def _load_cases():
    with open(TEST_JSON) as f:
        return json.load(f)


def _valid_codes():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return {r[0] for r in conn.execute("SELECT hs6 FROM hs_taxonomy")}
    finally:
        conn.close()


def _llm_on():
    from llm import backend_status
    s = backend_status()
    return bool(s["local_reachable"]) or s["fireworks_configured"]


def main():
    ap = argparse.ArgumentParser(description="Evaluate classify() against tests/test.json")
    ap.add_argument("n", nargs="?", type=int, default=None,
                    help="random batch size (default: run all cases)")
    ap.add_argument("--seed", type=int, default=None,
                    help="seed the random batch for reproducibility")
    args = ap.parse_args()

    cases = _load_cases()
    total_pool = len(cases)
    if args.n:
        rng = random.Random(args.seed)
        cases = rng.sample(cases, min(args.n, total_pool))

    valid = _valid_codes()
    n = len(cases)
    top1 = top3 = 0
    by_cat, by_src = {}, {}
    guard_violations = 0

    batch = f"batch of {n}/{total_pool}" if args.n else f"all {n}"
    print(f"Evaluating {batch} cases "
          f"(LLM {'on' if _llm_on() else 'OFF — keyword+vector only'})\n")
    print(f"{'expected':9} {'got':9} {'t1':3} {'t3':3} {'path':9} {'source':10} desc")
    print("-" * 92)

    for case in cases:
        desc = case["product_description"]
        gold = case["correct_hs6"]
        cat = case.get("category_tag", "?")
        res = classify(desc)
        cand_codes = [c["hs6"] for c in res["candidates"]]

        # hallucination guard: nothing may reference a non-existent code
        for code in ([res["hs6"]] if res["hs6"] else []) + cand_codes:
            if code not in valid:
                guard_violations += 1

        is_t1 = res["hs6"] == gold
        is_t3 = gold in cand_codes or is_t1
        top1 += is_t1
        top3 += is_t3
        c = by_cat.setdefault(cat, {"n": 0, "t1": 0, "t3": 0})
        c["n"] += 1
        c["t1"] += is_t1
        c["t3"] += is_t3

        src = res.get("source", "keyword+vector")
        by_src[src] = by_src.get(src, 0) + 1

        print(f"{gold:9} {str(res['hs6'] or '-'):9} "
              f"{'✓' if is_t1 else '·':3} {'✓' if is_t3 else '·':3} "
              f"{res['path']:9} {SRC_LABEL.get(src, src):10} {desc[:32]}")

    print("-" * 92)
    print(f"\nOVERALL  top-1 {top1}/{n} = {100*top1/n:.0f}%   "
          f"top-3 {top3}/{n} = {100*top3/n:.0f}%")
    for cat, c in sorted(by_cat.items()):
        print(f"  {cat:11} top-1 {c['t1']}/{c['n']}   top-3 {c['t3']}/{c['n']}")
    print("\nwho generated the answer:")
    for s, k in sorted(by_src.items(), key=lambda kv: -kv[1]):
        print(f"  {SRC_LABEL.get(s, s):12} {k}/{n}")
    print(f"\nhallucination-guard violations: {guard_violations} "
          f"(must be 0 — no invented codes)")


if __name__ == "__main__":
    main()
