import sqlite3
from pathlib import Path

# Anchor the DB to Tarrifpilot/Database/ regardless of the caller's CWD.
_ROOT = Path(__file__).resolve().parents[1]          # Tarrifpilot/
DB_PATH = str(_ROOT / "Database" / "tariff_pilot.db")


def init_sqlite_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Enable foreign key support in SQLite
    cursor.execute("PRAGMA foreign_keys = ON;")
    # WAL mode: lets readers work while a long ingest holds the write lock.
    # Persistent once set — survives across connections.
    cursor.execute("PRAGMA journal_mode = WAL;")

    # 0. hs_taxonomy Table (master list — what duty_rates & test_set reference)
    # The embedding lives in Chroma keyed by hs6; this SQL copy makes joins and
    # provenance queries possible without touching the vector store.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hs_taxonomy (
        hs6 TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        chapter TEXT NOT NULL,
        heading TEXT NOT NULL,
        category_tag TEXT CHECK(category_tag IN ('medical', 'ammunition', 'watches', 'liquor')),
        keywords TEXT,
        hs_revision TEXT
    );
    """)

    # 1. duty_rates Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS duty_rates (
        rate_id INTEGER PRIMARY KEY AUTOINCREMENT,
        hs6 TEXT NOT NULL,
        national_code TEXT,
        reporter_country TEXT NOT NULL,
        partner TEXT DEFAULT 'WLD',
        tariff_type TEXT CHECK(tariff_type IN ('MFN', 'PREF')),
        duty_type TEXT CHECK(duty_type IN ('ad_valorem', 'specific', 'compound')),
        ad_valorem_rate REAL,
        specific_amount REAL,
        specific_unit TEXT,
        currency TEXT,
        unit_of_quantity TEXT,
        source TEXT CHECK(source IN ('WITS', 'USITC', 'UK_TARIFF', 'TARIC', 'WTO_TDF', 'NATIONAL_MANUAL')),
        source_url TEXT NOT NULL,
        retrieved_date TEXT NOT NULL,
        effective_date TEXT,
        notes TEXT
    );
    """)

    # 2. restrictions_flags Table (Critical for Ammunition)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS restrictions_flags (
        flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
        hs6 TEXT NOT NULL,
        reporter_country TEXT NOT NULL,
        flag_type TEXT CHECK(flag_type IN ('license_required', 'prohibited', 'quota', 'permit')),
        description TEXT,
        source_url TEXT NOT NULL
    );
    """)

    # 3. change_log Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS change_log (
        change_id INTEGER PRIMARY KEY AUTOINCREMENT,
        hs6_or_chapter TEXT NOT NULL,
        reporter_country TEXT NOT NULL,
        old_rate REAL,
        new_rate REAL,
        change_date TEXT,
        implementation_date TEXT,
        source TEXT,
        source_url TEXT NOT NULL
    );
    """)

    # (The ground-truth test set is NOT a DB table — it lives in
    #  tests/test.json and is run by `python -m tests.evaluate`.)

    # Indexes for the hot lookup path (code -> rate for a country)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_duty_lookup ON duty_rates(hs6, reporter_country);")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_flags_lookup ON restrictions_flags(hs6, reporter_country);")

    conn.commit()
    conn.close()
    print("✓ SQLite database initialized successfully with structured tables.")


if __name__ == "__main__":
    init_sqlite_db()
