from __future__ import annotations
import sqlite3
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Gig:
    id: Optional[int]
    title: str
    slug: str
    url: str
    affiliate_url: str
    seller: str
    seller_level: str
    rating: float
    rating_count: int
    starting_price: float
    image_url: str
    category: str
    description: str
    first_seen_at: str
    last_seen_at: str
    is_active: int = 1


def _is_postgres(db_path: str) -> bool:
    return db_path.startswith("postgres")


def _get_pg_conn(db_path: str):
    import psycopg2
    return psycopg2.connect(db_path)


def _get_sqlite_conn(db_path: str):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _conn(db_path: str):
    if _is_postgres(db_path):
        return _get_pg_conn(db_path)
    return _get_sqlite_conn(db_path)


def init_db(db_path: str) -> None:
    pg = _is_postgres(db_path)
    serial = "SERIAL" if pg else "INTEGER"
    with _conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS gigs (
                id {serial} PRIMARY KEY,
                title TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                url TEXT UNIQUE NOT NULL,
                affiliate_url TEXT DEFAULT '',
                seller TEXT DEFAULT '',
                seller_level TEXT DEFAULT '',
                rating REAL DEFAULT 0,
                rating_count INTEGER DEFAULT 0,
                starting_price REAL DEFAULT 0,
                image_url TEXT DEFAULT '',
                category TEXT DEFAULT '',
                description TEXT DEFAULT '',
                first_seen_at TEXT,
                last_seen_at TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gigs_slug ON gigs(slug)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gigs_category ON gigs(category)")
        conn.commit()


def upsert_gigs(db_path: str, gigs: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    pg = _is_postgres(db_path)
    inserted = 0

    with _conn(db_path) as conn:
        cur = conn.cursor()
        for g in gigs:
            if pg:
                cur.execute("""
                    INSERT INTO gigs (title, slug, url, affiliate_url, seller, seller_level,
                        rating, rating_count, starting_price, image_url, category, description,
                        first_seen_at, last_seen_at, is_active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                    ON CONFLICT (url) DO UPDATE SET
                        title=EXCLUDED.title,
                        affiliate_url=EXCLUDED.affiliate_url,
                        seller=EXCLUDED.seller,
                        seller_level=EXCLUDED.seller_level,
                        rating=EXCLUDED.rating,
                        rating_count=EXCLUDED.rating_count,
                        starting_price=EXCLUDED.starting_price,
                        image_url=EXCLUDED.image_url,
                        category=EXCLUDED.category,
                        last_seen_at=EXCLUDED.last_seen_at,
                        is_active=1
                    RETURNING (xmax = 0) AS is_new
                """, (
                    g["title"], g["slug"], g["url"], g["affiliate_url"],
                    g["seller"], g["seller_level"], g["rating"], g["rating_count"],
                    g["starting_price"], g["image_url"], g["category"], g["description"],
                    now, now,
                ))
                row = cur.fetchone()
                if row and row[0]:
                    inserted += 1
            else:
                cur.execute("SELECT id FROM gigs WHERE url=?", (g["url"],))
                exists = cur.fetchone()
                if exists:
                    cur.execute("""
                        UPDATE gigs SET title=?, affiliate_url=?, seller=?, seller_level=?,
                            rating=?, rating_count=?, starting_price=?, image_url=?,
                            category=?, last_seen_at=?, is_active=1
                        WHERE url=?
                    """, (
                        g["title"], g["affiliate_url"], g["seller"], g["seller_level"],
                        g["rating"], g["rating_count"], g["starting_price"], g["image_url"],
                        g["category"], now, g["url"],
                    ))
                else:
                    cur.execute("""
                        INSERT INTO gigs (title, slug, url, affiliate_url, seller, seller_level,
                            rating, rating_count, starting_price, image_url, category, description,
                            first_seen_at, last_seen_at, is_active)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                    """, (
                        g["title"], g["slug"], g["url"], g["affiliate_url"],
                        g["seller"], g["seller_level"], g["rating"], g["rating_count"],
                        g["starting_price"], g["image_url"], g["category"], g["description"],
                        now, now,
                    ))
                    inserted += 1
        conn.commit()
    return inserted


def list_gigs(db_path: str, query: str = "", category: str = "",
              page: int = 1, per_page: int = 24) -> tuple[list[dict], int]:
    pg = _is_postgres(db_path)
    ph = "%s" if pg else "?"
    conditions = ["is_active=1"]
    params: list = []

    if query:
        conditions.append(f"(title LIKE {ph} OR seller LIKE {ph} OR description LIKE {ph})")
        like = f"%{query}%"
        params += [like, like, like]
    if category:
        conditions.append(f"category={ph}")
        params.append(category)

    where = " AND ".join(conditions)
    offset = (page - 1) * per_page

    with _conn(db_path) as conn:
        cur = conn.cursor()
        if pg:
            cur.execute(f"SELECT COUNT(*) FROM gigs WHERE {where}", params)
        else:
            cur.execute(f"SELECT COUNT(*) FROM gigs WHERE {where}", params)
        total = cur.fetchone()[0]

        order_sql = "rating DESC, rating_count DESC, first_seen_at DESC"
        if pg:
            cur.execute(
                f"SELECT * FROM gigs WHERE {where} ORDER BY {order_sql} LIMIT %s OFFSET %s",
                params + [per_page, offset],
            )
        else:
            cur.execute(
                f"SELECT * FROM gigs WHERE {where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                params + [per_page, offset],
            )
        rows = cur.fetchall()
        if pg:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows], total
        return [dict(r) for r in rows], total


def get_gig_by_slug(db_path: str, slug: str) -> Optional[dict]:
    pg = _is_postgres(db_path)
    ph = "%s" if pg else "?"
    with _conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM gigs WHERE slug={ph}", (slug,))
        row = cur.fetchone()
        if not row:
            return None
        if pg:
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        return dict(row)


def list_categories(db_path: str) -> list[str]:
    with _conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT category FROM gigs WHERE is_active=1 ORDER BY category")
        return [r[0] for r in cur.fetchall() if r[0]]


def get_feed_gigs(db_path: str, limit: int = 10, offset: int = 0) -> list[dict]:
    pg = _is_postgres(db_path)
    ph = "%s" if pg else "?"
    with _conn(db_path) as conn:
        cur = conn.cursor()
        if pg:
            cur.execute(
                "SELECT * FROM gigs WHERE is_active=1 ORDER BY first_seen_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
        else:
            cur.execute(
                "SELECT * FROM gigs WHERE is_active=1 ORDER BY first_seen_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = cur.fetchall()
        if pg:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        return [dict(r) for r in rows]
