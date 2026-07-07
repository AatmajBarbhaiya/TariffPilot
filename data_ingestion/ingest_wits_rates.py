import sqlite3
import requests
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Anchor the DB to Tarrifpilot/Database/ regardless of the caller's CWD.
_ROOT = Path(__file__).resolve().parents[1]          # Tarrifpilot/
DB_PATH = str(_ROOT / "Database" / "tariff_pilot.db")

# WITS is slow and I/O-bound, so we fetch many codes CONCURRENTLY (the CPU just
# waits on the network otherwise). Each request still retries with backoff.
MAX_WORKERS = 8              # concurrent WITS requests — polite but ~8x faster
REQUEST_TIMEOUT = 45         # seconds per attempt
MAX_RETRIES = 4             # attempts per URL before giving up on that code

# requests.Session is not thread-safe to share, so give each worker its own.
_local = threading.local()


def _session():
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        _local.session = s
    return s


def fetch_json(url, headers):
    """GET a WITS URL, retrying on timeout / connection / 5xx. Silent (so it
    doesn't garble the progress bar); returns parsed JSON dict or None."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session().get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code < 500:      # 4xx = our fault, don't retry
                return None
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.RequestException):
            pass
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)        # 2s, 4s, 8s ...
    return None


# WITS reporter codes (ISO numeric). EU is reported as "918" in WITS/UNCTAD TRAINS.
TARGET_COUNTRIES = [
    {"iso_numeric": "840", "iso3": "USA"},  # United States
    {"iso_numeric": "826", "iso3": "GBR"},  # United Kingdom
    {"iso_numeric": "918", "iso3": "EU"},   # European Union
]

YEAR = "2022"

# ---------------------------------------------------------------------------
# SDMX-JSON observation layout for TRN / reported.
# The observation array is [OBS_VALUE, <attributes in structure order>].
# Verified attribute order:
#   0  OBS_VALUE          -> ad-valorem simple average (%)
#   7  TOTALNOOFLINES
#   10 NBR_NA_LINES       -> tariff lines with no ad-valorem value (specific duties)
# ---------------------------------------------------------------------------
IDX_OBS_VALUE = 0
IDX_TOTAL_LINES = 7
IDX_NA_LINES = 10


def _num(arr, idx, default=0.0):
    """Safely pull a float from the positional SDMX array."""
    try:
        v = arr[idx]
        return float(v) if v is not None else default
    except (IndexError, ValueError, TypeError):
        return default


def get_scoped_codes(conn):
    """Drive ingestion from the taxonomy table instead of a hardcoded list."""
    return [r[0] for r in conn.execute(
        "SELECT hs6 FROM hs_taxonomy ORDER BY hs6")]


def process_task(task, retrieved_date, headers):
    """Fetch + parse one (country, hs6). Runs in a worker thread — NO DB access.
    Returns an INSERT tuple for duty_rates, or None if there's no usable rate."""
    country, hs6 = task
    url = (
        "https://wits.worldbank.org/API/V1/SDMX/V21/datasource/TRN/"
        f"reporter/{country['iso_numeric']}/partner/000/product/{hs6}/"
        f"year/{YEAR}/datatype/reported?format=JSON"
    )
    payload = fetch_json(url, headers)
    if payload is None:
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

    ad_valorem = _num(row, IDX_OBS_VALUE)
    na_lines = _num(row, IDX_NA_LINES)
    total_lines = _num(row, IDX_TOTAL_LINES)

    # NBR_NA_LINES > 0 => some lines carry a specific/compound rate with no
    # ad-valorem value, so the ad_valorem number alone understates the duty.
    if na_lines > 0 and ad_valorem > 0:
        duty_type = "compound"
    elif na_lines > 0:
        duty_type = "specific"
    else:
        duty_type = "ad_valorem"

    note = (f"WITS simple-average ad-valorem. lines={int(total_lines)}, "
            f"specific/NA lines={int(na_lines)}.")
    if na_lines > 0:
        note += (" NEEDS ENRICHMENT: specific/compound duty not fully captured "
                 "by WITS ad-valorem — enrich from national source "
                 "(USITC/UK Tariff/TARIC).")

    return (hs6, country['iso3'], 'WLD', 'MFN', duty_type, ad_valorem,
            'WITS', url, retrieved_date, note)


def _bar(done, total, hits, msg=""):
    """One-line progress bar rendered in place with \\r."""
    width = 30
    filled = int(width * done / total) if total else width
    pct = 100 * done / total if total else 100
    print(f"\r[{'█' * filled}{'░' * (width - filled)}] "
          f"{done}/{total} ({pct:4.1f}%)  hits:{hits}  {msg:<22}",
          end="", flush=True)


def run_wits_ingestion():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    codes = get_scoped_codes(conn)
    if not codes:
        raise SystemExit("hs_taxonomy is empty — run ingest_taxonomy.py first.")

    retrieved_date = datetime.now().strftime("%Y-%m-%d")
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    # Idempotency: clear prior WITS rows so re-runs don't accumulate duplicates.
    cursor.execute("DELETE FROM duty_rates WHERE source = 'WITS'")
    conn.commit()

    tasks = [(c, hs6) for c in TARGET_COUNTRIES for hs6 in codes]
    total = len(tasks)
    print(f"Ingesting {len(codes)} codes x {len(TARGET_COUNTRIES)} reporters "
          f"= {total} calls, {MAX_WORKERS} at a time...")

    done = hits = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_task, t, retrieved_date, headers): t
                   for t in tasks}
        for fut in as_completed(futures):
            done += 1
            country, hs6 = futures[fut]
            try:
                result = fut.result()
            except Exception:
                result = None
            if result:
                cursor.execute("""
                    INSERT INTO duty_rates (
                        hs6, reporter_country, partner, tariff_type,
                        duty_type, ad_valorem_rate, source, source_url,
                        retrieved_date, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, result)
                hits += 1
                if hits % 20 == 0:
                    conn.commit()   # periodic save so a crash isn't total loss
            _bar(done, total, hits, f"{country['iso3']} {hs6}")

    conn.commit()
    conn.close()
    elapsed = time.time() - started
    print(f"\n✓ Done in {elapsed:.0f}s. {hits} rate rows recorded "
          f"({total - hits} codes had no reported rate).")


if __name__ == "__main__":
    run_wits_ingestion()
