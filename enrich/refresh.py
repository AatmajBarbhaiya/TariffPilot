"""Orchestrate enrichment: 85 HS6 × {USITC, UK_TARIFF} → duty_rates.

Idempotent per source (deletes its own rows first, like the WITS ingest), so
re-running just refreshes. Additive: WITS rows are left untouched.

Usage (from project root Tariffpilot_rag_db/):
  python -m enrich.refresh                 # both sources
  python -m enrich.refresh USITC           # one source
"""
import sqlite3
import sys
from datetime import datetime

import config
from enrich import us_adapter, uk_adapter

ADAPTERS = {"USITC": us_adapter, "UK_TARIFF": uk_adapter}

_INSERT = """
INSERT INTO duty_rates (
    hs6, national_code, reporter_country, partner, tariff_type, duty_type,
    ad_valorem_rate, specific_amount, specific_unit, currency,
    unit_of_quantity, source, source_url, retrieved_date, notes
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _row(dl, today):
    return (dl.hs6, dl.national_code, dl.reporter, "WLD", dl.tariff_type,
            dl.duty_type, dl.ad_valorem_rate, dl.specific_amount, dl.specific_unit,
            dl.currency, dl.unit_of_quantity, dl.source, dl.source_url, today,
            dl.notes)


def run(sources=("USITC", "UK_TARIFF")):
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    codes = [r[0] for r in cur.execute("SELECT hs6 FROM hs_taxonomy ORDER BY hs6")]
    today = datetime.now().strftime("%Y-%m-%d")

    for src in sources:
        adapter = ADAPTERS[src]
        cur.execute("DELETE FROM duty_rates WHERE source = ?", (src,))
        conn.commit()

        hits = misses = 0
        total = len(codes)
        print(f"\n{src}: enriching {total} codes...")
        for i, hs6 in enumerate(codes, 1):
            try:
                dl = adapter.enrich_code(hs6)
            except Exception as e:
                dl = None
                print(f"  ! {hs6}: {type(e).__name__}: {e}")
            if dl:
                cur.execute(_INSERT, _row(dl, today))
                hits += 1
                if hits % 10 == 0:
                    conn.commit()
            else:
                misses += 1
            print(f"\r  {i}/{total}  hits:{hits}  misses:{misses}  ({hs6})   ",
                  end="", flush=True)
        conn.commit()
        print(f"\n  ✓ {src}: {hits} rows written, {misses} codes had no national line.")

    conn.close()


if __name__ == "__main__":
    srcs = tuple(a for a in sys.argv[1:] if a in ADAPTERS) or ("USITC", "UK_TARIFF")
    run(srcs)
