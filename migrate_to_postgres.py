"""
migrate_to_postgres.py — copy local SQLite data to Postgres for Vercel.

Usage:
    $env:DATABASE_URL="postgresql://user:pass@host/dbname"
    python migrate_to_postgres.py
"""
import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(__file__))

from app.db import init_db

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = "./data/fiverr.sqlite3"

if not DATABASE_URL:
    print("ERROR: set DATABASE_URL env var first.")
    sys.exit(1)

import psycopg2

print(f"Connecting to Postgres...")
pg = psycopg2.connect(DATABASE_URL)
init_db(DATABASE_URL)

print(f"Reading from {SQLITE_PATH}...")
sq = sqlite3.connect(SQLITE_PATH)
sq.row_factory = sqlite3.Row
rows = sq.execute("SELECT * FROM gigs").fetchall()
print(f"Found {len(rows)} gigs in SQLite")

cur = pg.cursor()
inserted = 0
for r in rows:
    cur.execute("""
        INSERT INTO gigs (title, slug, url, affiliate_url, seller, seller_level,
            rating, rating_count, starting_price, image_url, category, description,
            first_seen_at, last_seen_at, is_active)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (url) DO NOTHING
    """, (
        r["title"], r["slug"], r["url"], r["affiliate_url"],
        r["seller"], r["seller_level"], r["rating"], r["rating_count"],
        r["starting_price"], r["image_url"], r["category"], r["description"],
        r["first_seen_at"], r["last_seen_at"], r["is_active"],
    ))
    if cur.rowcount:
        inserted += 1

pg.commit()
pg.close()
sq.close()
print(f"Done — {inserted} gigs migrated to Postgres.")
