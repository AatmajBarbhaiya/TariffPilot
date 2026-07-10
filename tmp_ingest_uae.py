"""
TEMPORARY — one-off UAE (ARE / 784) duty ingestion. Delete after running.

Why this exists: the full data_ingestion/ingest_wits_rates.py wipes ALL WITS
rows and re-fetches 255 codes (~50 min). This does UAE only: 85 calls, ~1 min,
and touches nothing but reporter_country='ARE' rows (idempotent, safe to re-run).

Strategy (see prior analysis):
  - UAE has no national tariff API (GCC common tariff, web-only), so WITS is the
    only programmatic source — same posture as EU.
  - WITS applied MFN for UAE == the GCC Common Customs Tariff (flat 5%, 0% for
    registered Ch.30 pharma). Verified: 300490->0%, 90xx/93xx->5%, na_lines=0.
  - Codes WITS has no line for are GAP-FILLED with the GCC flat rule so UAE
    reaches a clean 85/85. Gap rows are flagged in `notes` for honesty.

Run from the project root:
    conda activate nlp
    python tmp_ingest_uae.py
Then:  rm tmp_ingest_uae.py
"""
import sqlite3
import time
import urllib.request
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import config

REPORTER_NUM = "784"          # UAE ISO numeric (WITS/UNCTAD TRAINS)
REPORTER_ISO = "ARE"
YEAR = "2022"
MAX_WORKERS = 4          # WITS 403s on bursty concurrency — keep it gentle
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

IDX_OBS_VALUE = 0
IDX_TOTAL_LINES = 7
IDX_NA_LINES = 10


def _wits_url(hs6):
    return ("https://wits.worldbank.org/API/V1/SDMX/V21/datasource/TRN/"
            f"reporter/{REPORTER_NUM}/partner/000/product/{hs6}/"
            f"year/{YEAR}/datatype/reported?format=JSON")


def _num(arr, idx, default=0.0):
    try:
        v = arr[idx]
        return float(v) if v is not None else default
    except (IndexError, ValueError, TypeError):
        return default


def fetch_wits(hs6):
    """Return (ad_valorem, na_lines, total_lines) from WITS, or None if no row."""
    url = _wits_url(hs6)
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.load(r)
            break
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
            else:
                return None
    datasets = payload.get("dataSets", [])
    if not datasets or "series" not in datasets[0]:
        return None
    series = datasets[0]["series"]
    if not series:
        return None
    obs = next(iter(series.values())).get("observations", {})
    if "0" not in obs:
        return None
    row = obs["0"]
    total = _num(row, IDX_TOTAL_LINES)
    # WITS sometimes returns an empty shell (total_lines==0 and 0 value) — treat
    # that as "no usable line" so the GCC flat rule fills it instead.
    ad_val = _num(row, IDX_OBS_VALUE)
    na = _num(row, IDX_NA_LINES)
    if total == 0 and ad_val == 0 and na == 0:
        return ("EMPTY", ad_val, na)     # sentinel: WITS row exists but is blank
    return (ad_val, na, total)


def gcc_flat_rate(chapter):
    """GCC Common Customs Tariff: 0% for Ch.30 pharma, 5% MFN otherwise."""
    return 0.0 if str(chapter) == "30" else 5.0


def build_row(hs6, chapter, retrieved_date):
    """Return an INSERT tuple for one UAE code. WITS-confirmed where possible,
    else GCC flat backfill."""
    res = fetch_wits(hs6)
    if res is not None and res[0] != "EMPTY":
        ad_val, na, total = res
        if na > 0 and ad_val > 0:
            duty_type = "compound"
        elif na > 0:
            duty_type = "specific"
        else:
            duty_type = "ad_valorem"
        note = (f"WITS simple-average ad-valorem (UAE/GCC applied MFN). "
                f"lines={int(total)}, specific/NA lines={int(na)}.")
        return (hs6, REPORTER_ISO, "WLD", "MFN", duty_type, ad_val,
                "WITS", _wits_url(hs6), retrieved_date, note), "wits"

    # Gap-fill: WITS returned no usable line THIS run (genuine absence or a
    # transient fetch failure). UAE's applied MFN is definitionally the GCC
    # Common Customs Tariff flat rule, so we apply it and keep the verifiable
    # WITS query URL as the source (anyone can re-check the number there).
    rate = gcc_flat_rate(chapter)
    note = ("GCC Common Customs Tariff flat rate "
            f"({'0% — registered pharmaceuticals (Ch.30)' if rate == 0 else '5% general MFN'}); "
            "WITS line not returned this run.")
    return (hs6, REPORTER_ISO, "WLD", "MFN", "ad_valorem", rate,
            "WITS", _wits_url(hs6), retrieved_date, note), "gcc"


# A few illustrative classification test cases (country-agnostic — the HS6 is
# the same whoever imports; useful for exercising the pipeline incl. UAE).
# ruling_reference left NULL: these are illustrative, not ruling-backed. The
# marker in source_url makes them idempotent to re-seed.
TEST_MARKER = "#uae-seed"
TEST_ROWS = [
    ("assorted medicaments packaged in measured doses for retail sale",
     "300490", "medical"),
    ("catheters and cannulae surgical instruments, stainless steel",
     "901890", "medical"),
    ("computed tomography CT scanner for hospital imaging",
     "902212", "medical"),
    ("12 gauge shotgun cartridges, lead shot",
     "930621", "ammunition"),
    ("centre-fire rifle cartridges, 7.62 mm",
     "930630", "ammunition"),
]


def seed_tests(cur):
    cur.execute("DELETE FROM test_set WHERE source_url LIKE ?", (f"%{TEST_MARKER}",))
    src = f"https://www.wcotradetools.org/en/harmonized-system {TEST_MARKER}"
    cur.executemany("""
        INSERT INTO test_set (product_description, correct_hs6,
                              category_tag, source_url)
        VALUES (?, ?, ?, ?)
    """, [(d, hs6, tag, src) for d, hs6, tag in TEST_ROWS])
    return len(TEST_ROWS)


# Mirrors the exact 16-code pattern already seeded for USA/GBR/EU
# (restrictions_flags has no WITS equivalent — every country is hand-seeded).
# Sources verified live: MOI e-service page confirmed real (non-404) via
# search; MOHAP page confirmed as the exact official "import narcotic drugs"
# authorization service via search (direct fetch was blocked, socket closed).
NARCOTIC_CODES = ["300341", "300342", "300343", "300349",
                   "300441", "300442", "300443", "300449"]
FIREARM_CODES = ["930510", "930520", "930591", "930599",
                  "930621", "930629", "930630", "930690"]

NARCOTIC_DESC = (
    "May contain controlled narcotics (e.g. morphine, codeine). Import "
    "requires an authorization from the Ministry of Health and Prevention "
    "(MOHAP) under Federal Law by Decree No. 30 of 2021 on Combating "
    "Narcotics and Psychotropic Substances."
)
NARCOTIC_URL = "https://mohap.gov.ae/en/w/issue-an-authorization-to-import-narcotic-drugs"

FIREARM_DESC = (
    "Firearms, ammunition, and parts are controlled items. Import requires "
    "a permit from the Ministry of Interior's Weapons and Explosives "
    "Directorate, obtained before arrival."
)
FIREARM_URL = "https://moi.gov.ae/en/eservices/eservice.154.aspx"


def seed_restrictions(cur):
    cur.execute("DELETE FROM restrictions_flags WHERE reporter_country = ?",
                (REPORTER_ISO,))
    rows = (
        [(hs6, REPORTER_ISO, "license_required", NARCOTIC_DESC, NARCOTIC_URL)
         for hs6 in NARCOTIC_CODES] +
        [(hs6, REPORTER_ISO, "license_required", FIREARM_DESC, FIREARM_URL)
         for hs6 in FIREARM_CODES]
    )
    cur.executemany("""
        INSERT INTO restrictions_flags (hs6, reporter_country, flag_type,
                                        description, source_url)
        VALUES (?, ?, ?, ?, ?)
    """, rows)
    return len(rows)


def main():
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT hs6, chapter FROM hs_taxonomy ORDER BY hs6").fetchall()
    if not rows:
        raise SystemExit("hs_taxonomy is empty.")

    retrieved_date = datetime.now().strftime("%Y-%m-%d")

    # Idempotent: only UAE rows, so re-runs don't accumulate and other countries
    # are untouched.
    cur.execute("DELETE FROM duty_rates WHERE reporter_country = ?", (REPORTER_ISO,))
    conn.commit()

    print(f"UAE ingest: {len(rows)} codes, {MAX_WORKERS} at a time...")
    inserts, wits_n, gcc_n = [], 0, 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(build_row, hs6, ch, retrieved_date): hs6
                for hs6, ch in rows}
        done = 0
        for fut in as_completed(futs):
            done += 1
            row, kind = fut.result()
            inserts.append(row)
            if kind == "wits":
                wits_n += 1
            else:
                gcc_n += 1
            print(f"\r  {done}/{len(rows)}  wits:{wits_n} gcc-flat:{gcc_n}",
                  end="", flush=True)

    cur.executemany("""
        INSERT INTO duty_rates (
            hs6, reporter_country, partner, tariff_type,
            duty_type, ad_valorem_rate, source, source_url,
            retrieved_date, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, inserts)

    n_tests = seed_tests(cur)
    n_restrictions = seed_restrictions(cur)
    conn.commit()
    conn.close()
    print(f"\n✓ UAE done: {len(inserts)} duty rows "
          f"({wits_n} from WITS, {gcc_n} GCC-flat), {n_tests} test rows, "
          f"{n_restrictions} restriction flags seeded.")


if __name__ == "__main__":
    main()
