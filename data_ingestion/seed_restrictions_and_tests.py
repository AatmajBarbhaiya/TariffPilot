"""
Seed the two tables the WITS pipeline can't fill: restrictions_flags and test_set.

PROVENANCE NOTE
---------------
Every row must carry a real, checkable source_url (the project's golden rule).
The 3 ammunition restriction URLs below were manually verified live (USA/ATF,
EU/EUR-Lex, GBR/gov.uk). CROSS ruling references are left as 'TODO-CROSS'
because a credible test set needs REAL ruling numbers pulled from
https://rulings.cbp.gov — fill those in rather than inventing them.
"""
import sqlite3
from pathlib import Path

# Anchor the DB to Tarrifpilot/Database/ regardless of the caller's CWD.
_ROOT = Path(__file__).resolve().parents[1]          # Tarrifpilot/
DB_PATH = str(_ROOT / "Database" / "tariff_pilot.db")

# =============================================================================
# RESTRICTION FLAGS — flag_type in license_required | prohibited | quota | permit
#
# Built from three groups (see build_restrictions()). All source URLs below were
# manually / search-index verified live. Rows are generated per code x country
# so a correct classification of ANY restricted code surfaces the warning, not
# just the one code we happened to hand-enter first.
# =============================================================================

# --- Source URLs, verified live ---------------------------------------------
# Firearms family (ammunition Ch.9306 AND firearm parts Ch.9305). The same three
# authorities cover both: the ATF import page explicitly names "firearm component
# parts", the EU Directive covers "essential components", the UK Act covers
# component parts — so ammo and parts share these URLs.
FIREARMS_SRC = {
    "USA": "https://www.atf.gov/firearms/tools-and-services-firearms-industry/current-licensees/import-firearms-ammunition-and-defense-articles",
    "EU":  "https://eur-lex.europa.eu/eli/dir/2021/555/oj",
    "GBR": "https://www.gov.uk/guidance/firearms-licensing-police-guidance",
}
# Controlled-substance family (alkaloid medicaments that MAY be narcotics).
DRUGS_SRC = {
    "USA": "https://www.dea.gov/drug-information/csa",
    "EU":  "https://www.euda.europa.eu/drugs-library/single-convention-narcotic-drugs-1961_en",
    "GBR": "https://www.gov.uk/guidance/controlled-drugs-import-and-export-licences",
}

# --- Per-(group, country) description text -----------------------------------
DESCRIPTIONS = {
    ("ammo", "USA"): "Import of ammunition regulated by ATF under the Gun Control Act; an approved ATF Form 6 import permit is required.",
    ("ammo", "EU"):  "Ammunition controlled under EU Firearms Directive (EU) 2021/555; import authorisation required.",
    ("ammo", "GBR"): "Ammunition is a controlled item under the Firearms Act 1968; a valid firearm/shotgun certificate is required.",
    ("parts", "USA"): "Firearm component parts are USMIL defense articles; CBP will not release them without an approved ATF Form 6 import permit.",
    ("parts", "EU"):  "Essential components of firearms are controlled under EU Firearms Directive (EU) 2021/555; import authorisation required.",
    ("parts", "GBR"): "Component parts of firearms are controlled under the Firearms Act 1968.",
    # Conditional wording: "containing alkaloids" is NOT automatically a narcotic
    # (caffeine, atropine are alkaloids too). Flag surfaces the risk to check.
    ("drug", "USA"): "May contain controlled narcotics (e.g. morphine, codeine). Importing controlled-substance formulations requires DEA registration and an import permit under the Controlled Substances Act.",
    ("drug", "EU"):  "May contain controlled narcotics. Importing controlled-substance formulations requires an import authorisation from the national competent authority under the 1961 UN Single Convention framework.",
    ("drug", "GBR"): "May contain controlled narcotics. Importing controlled-substance formulations requires a Home Office import licence under the Misuse of Drugs Act 1971.",
}

COUNTRIES = ("USA", "EU", "GBR")

# --- Code lists. Ammo/parts are the full Ch.93 headings in scope; alkaloid
#     medicament codes are derived from the taxonomy at runtime (see below). ---
AMMO_CODES = ["930621", "930629", "930630",
              "930690"]      # Group A — ammunition
PARTS_CODES = ["930510", "930520", "930591",
               "930599"]     # Group B — firearm parts


def build_restrictions(conn):
    """Generate (hs6, country, flag_type, description, source_url) rows for all
    three restricted groups, skipping any code not present in hs_taxonomy."""
    known = {r[0] for r in conn.execute("SELECT hs6 FROM hs_taxonomy")}

    # Group C — derive alkaloid medicament codes straight from the taxonomy so
    # we flag exactly the codes that exist, no more.
    # Match "containing alkaloids" but NOT "(not containing ... alkaloids)"
    # (e.g. 300390) — the negation would otherwise be a false positive.
    drug_codes = [r[0] for r in conn.execute(
        "SELECT hs6 FROM hs_taxonomy "
        "WHERE category_tag='medical' "
        "AND lower(description) LIKE '%containing alkaloid%' "
        "AND lower(description) NOT LIKE '%not containing%'")]

    rows = []
    for group, codes, src in (
        ("ammo", AMMO_CODES, FIREARMS_SRC),
        ("parts", PARTS_CODES, FIREARMS_SRC),
        ("drug", drug_codes, DRUGS_SRC),
    ):
        for hs6 in codes:
            if hs6 not in known:
                continue  # don't flag a code we don't actually carry
            for country in COUNTRIES:
                rows.append((hs6, country, "license_required",
                             DESCRIPTIONS[(group, country)], src[country]))
    return rows


# NOTE: the ground-truth TEST SET now lives in tests/test.json (edit it there)
# and is run by `python -m tests.evaluate`. It is no longer stored in the DB.


def seed():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Idempotent: wipe seeded rows first (safe — seed-only table).
    cur.execute("DELETE FROM restrictions_flags")

    restrictions = build_restrictions(conn)
    cur.executemany("""
        INSERT INTO restrictions_flags
            (hs6, reporter_country, flag_type, description, source_url)
        VALUES (?, ?, ?, ?, ?)
    """, restrictions)

    conn.commit()
    n_flags = cur.execute(
        'SELECT COUNT(*) FROM restrictions_flags').fetchone()[0]
    n_codes = cur.execute(
        'SELECT COUNT(DISTINCT hs6) FROM restrictions_flags').fetchone()[0]
    print(
        f"✓ restrictions_flags: {n_flags} rows across {n_codes} codes x {len(COUNTRIES)} countries")
    conn.close()


if __name__ == "__main__":
    seed()
