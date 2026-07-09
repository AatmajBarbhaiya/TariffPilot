"""
Result card: hs6 (+ reporter country) -> fully sourced answer.

Same joins proven end-to-end in sanity_check.py, returned as a dict for the API
instead of printed. Provenance rule: every duty and restriction carries its
source_url; the "NEEDS ENRICHMENT" warning is surfaced for specific/compound.
"""
import sqlite3

import config


def build_card(hs6, country):
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        tax = cur.execute("SELECT * FROM hs_taxonomy WHERE hs6=?", (hs6,)).fetchone()
        if not tax:
            return None

        card = {
            "hs6": hs6,
            "country": country,
            "category": tax["category_tag"],
            "description": tax["description"],
            "chapter": tax["chapter"],
            "heading": tax["heading"],
            "duty": None,
            "restrictions": [],
        }

        rate = cur.execute(
            "SELECT * FROM duty_rates WHERE hs6=? AND reporter_country=? "
            "ORDER BY rate_id DESC LIMIT 1", (hs6, country)).fetchone()
        if rate:
            enrich = rate["duty_type"] in ("specific", "compound")
            card["duty"] = {
                "ad_valorem_rate": rate["ad_valorem_rate"],
                "duty_type": rate["duty_type"],
                "tariff_type": rate["tariff_type"],
                "partner": rate["partner"],
                "specific_amount": rate["specific_amount"],
                "specific_unit": rate["specific_unit"],
                "currency": rate["currency"],
                "source": rate["source"],
                "source_url": rate["source_url"],
                "needs_enrichment": enrich,
                "warning": ("Specific/compound duty — ad-valorem alone "
                            "understates it; enrich from national source."
                            if enrich else None),
            }

        for f in cur.execute(
            "SELECT * FROM restrictions_flags WHERE hs6=? AND reporter_country=?",
            (hs6, country)):
            card["restrictions"].append({
                "flag_type": f["flag_type"],
                "description": f["description"],
                "source_url": f["source_url"],
            })

        return card
    finally:
        conn.close()
