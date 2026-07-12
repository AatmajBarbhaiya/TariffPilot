import requests
import io
import csv
import sqlite3
from pathlib import Path
# Chroma setup helper. Import is relative to the project root
# (Tariffpilot_rag_db/), so run everything from there — same convention as
# `retrieval` and `config` (see README §1).
from data_ingestion.ingest_db import get_chroma_collection

# Anchor the DB to <project>/Database/ regardless of the caller's CWD.
_ROOT = Path(__file__).resolve().parents[1]          # Tariffpilot_rag_db/
DB_PATH = str(_ROOT / "Database" / "tariff_pilot.db")
TAXONOMY_URL = "https://raw.githubusercontent.com/datasets/harmonized-system/master/data/harmonized-system.csv"


def classify(code):
    """Return category_tag for an in-scope 6-digit code, else None."""
    chapter = code[:2]
    heading = code[:4]

    # Scope rule: Medical (Ch. 30 + Ch. 90 headings 9018-9022)
    if chapter == "30":
        return "medical"
    if chapter == "90" and heading in ("9018", "9019", "9020", "9021", "9022"):
        return "medical"
    # Scope rule: Ammunition (Ch. 93 headings 9305, 9306)
    if chapter == "93" and heading in ("9305", "9306"):
        return "ammunition"
    # Scope rule: Watches (Ch. 91 headings 9101, 9102 — finished wrist/pocket
    # watches only; clocks (9103+) and watch parts/cases/straps (9108-9114)
    # are deliberately excluded to keep scope tight, same as the ammo rule
    # above only takes 2 of chapter 93's headings.)
    if chapter == "91" and heading in ("9101", "9102"):
        return "watches"
    # Scope rule: Liquor (Ch. 22 heading 2208 — distilled spirits only; wine/
    # beer/vinegar elsewhere in chapter 22 are out of scope.)
    if chapter == "22" and heading == "2208":
        return "liquor"
    return None


def download_and_filter_taxonomy():
    print("Downloading HS Taxonomy dataset...")
    response = requests.get(TAXONOMY_URL, timeout=60)
    if response.status_code != 200:
        raise Exception(
            f"Failed to retrieve data. Status code: {response.status_code}")

    reader = csv.DictReader(io.StringIO(response.text))

    documents, metadatas, ids = [], [], []
    sql_rows = []

    print("Filtering rows for Medical and Ammunition categories...")

    for row in reader:
        code = row.get('hscode', '').strip()
        description = row.get('description', '').strip()

        # We only want precise 6-digit codes
        if len(code) != 6:
            continue

        category_tag = classify(code)
        if not category_tag:
            continue

        chapter, heading = code[:2], code[:4]
        documents.append(description)
        ids.append(code)
        metadatas.append({
            "hs6": code,
            "chapter": chapter,
            "heading": heading,
            "category_tag": category_tag,
            "hs_revision": "H6 / 2022",
        })
        sql_rows.append((code, description, chapter, heading,
                         category_tag, None, "H6 / 2022"))

    if not documents:
        print("⚠ No matching codes found during filtering process.")
        return

    # 1) Vector store — upsert so re-runs don't crash on duplicate IDs.
    collection = get_chroma_collection()
    print(f"Upserting {len(documents)} focused vectors into Chroma...")
    collection.upsert(documents=documents, metadatas=metadatas, ids=ids)
    print(f"✓ Chroma total elements in space: {collection.count()}")

    # 2) Relational mirror — lets duty_rates / test_set join against a real
    #    taxonomy table and keeps descriptions queryable in SQL.
    conn = sqlite3.connect(DB_PATH)
    conn.executemany("""
        INSERT INTO hs_taxonomy
            (hs6, description, chapter, heading, category_tag, keywords, hs_revision)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(hs6) DO UPDATE SET
            description=excluded.description,
            chapter=excluded.chapter,
            heading=excluded.heading,
            category_tag=excluded.category_tag,
            hs_revision=excluded.hs_revision
    """, sql_rows)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM hs_taxonomy").fetchone()[0]
    conn.close()
    print(f"✓ hs_taxonomy (SQLite) now holds {count} codes.")


if __name__ == "__main__":
    download_and_filter_taxonomy()
