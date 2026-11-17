import os
import re
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://beats:beats@db:5432/beats")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row, autocommit=True)


def norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_killed(conn) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key='killed'").fetchone()
    return bool(row and row["value"] is True)


def log_event(conn, level, kind, message):
    conn.execute("INSERT INTO events(level, kind, message) VALUES (%s,%s,%s)", (level, kind, message))


def upsert_artist(conn, name, via, priority=5, mentions_inc=0):
    key = norm_key(name)
    if len(key) < 3:
        return None
    row = conn.execute(
        """INSERT INTO artists(name, key, discovered_via, priority, title_mentions)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (key) DO UPDATE
             SET title_mentions = artists.title_mentions + EXCLUDED.title_mentions,
                 priority = LEAST(artists.priority, EXCLUDED.priority)
           RETURNING id""",
        (name, key, via, priority, mentions_inc),
    ).fetchone()
    return row["id"]


MIGRATIONS = [
    "ALTER TABLE beats ADD COLUMN IF NOT EXISTS genre TEXT",
    "ALTER TABLE beats ADD COLUMN IF NOT EXISTS caption TEXT",
    "ALTER TABLE beats ADD COLUMN IF NOT EXISTS video_path TEXT",
    "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS description TEXT",
    "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS tags TEXT[]",
    "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS is_test BOOLEAN DEFAULT false",
    "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS title_locked BOOLEAN DEFAULT false",
    "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS error TEXT",
]


def ensure_schema(conn):
    for m in MIGRATIONS:
        conn.execute(m)
