"""
End-to-end lookup + provenance check (Day-1 checklist, step 7).

For a given HS6 + reporter country, resolve:
  taxonomy description  ->  duty rate (+ type)  ->  restrictions  ->  source URLs

Everything printed must carry a source_url; a rate with no provenance is a bug.
Run after ingest_taxonomy.py, ingest_wits_rates.py and seed_restrictions_and_tests.py.
"""
import sqlite3
import sys
from pathlib import Path

# Anchor the DB to Tarrifpilot/Database/ regardless of the caller's CWD.
DB_PATH = str(Path(__file__).resolve().parent / "Database" / "tariff_pilot.db")


def lookup(hs6, country):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tax = cur.execute(
        "SELECT * FROM hs_taxonomy WHERE hs6=?", (hs6,)).fetchone()
    print(f"\n=== {hs6} / {country} ===")
    if not tax:
        print("  ✗ code not in taxonomy (out of scope?)")
        conn.close()
        return
    print(f"  {tax['category_tag'].upper()}: {tax['description']}")

    rate = cur.execute(
        "SELECT * FROM duty_rates WHERE hs6=? AND reporter_country=? "
        "ORDER BY rate_id DESC LIMIT 1", (hs6, country)).fetchone()
    if rate:
        print(f"  Duty: {rate['ad_valorem_rate']}% ({rate['duty_type']}, "
              f"{rate['tariff_type']}, partner={rate['partner']})")
        print(f"    source[{rate['source']}]: {rate['source_url']}")
        if rate['duty_type'] in ("specific", "compound"):
            print("    ⚠ specific/compound — ad-valorem alone understates duty; "
                  "enrich from national source.")
    else:
        print("  ✗ no duty rate on file for this reporter")

    flags = cur.execute(
        "SELECT * FROM restrictions_flags WHERE hs6=? AND reporter_country=?",
        (hs6, country)).fetchall()
    for f in flags:
        print(f"  🔒 {f['flag_type']}: {f['description']}")
        print(f"    source: {f['source_url']}")

    conn.close()


def coverage():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    print("=== coverage ===")
    for label, q in [
        ("hs_taxonomy codes", "SELECT COUNT(*) FROM hs_taxonomy"),
        ("duty_rates rows", "SELECT COUNT(*) FROM duty_rates"),
        ("  reporters", "SELECT COUNT(DISTINCT reporter_country) FROM duty_rates"),
        ("  specific/compound", "SELECT COUNT(*) FROM duty_rates WHERE duty_type!='ad_valorem'"),
        ("  missing provenance", "SELECT COUNT(*) FROM duty_rates WHERE source_url IS NULL OR source_url=''"),
        ("restrictions_flags", "SELECT COUNT(*) FROM restrictions_flags"),
        # test set moved to tests/test.json (run: python -m tests.evaluate)
    ]:
        print(f"  {label}: {cur.execute(q).fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    coverage()
    samples = [("300490", "USA"), ("901831", "EU"), ("930630", "USA")]
    if len(sys.argv) == 3:
        samples = [(sys.argv[1], sys.argv[2])]
    for hs6, country in samples:
        lookup(hs6, country)
