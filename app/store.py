"""SQLite persistence: cached scans, the waitlist, and rate limiting.

SQLite because this has one writer and modest traffic, and because a product
with no customers should not be paying for a managed database. When that stops
being true, the swap is one module.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.getenv("FALSEGREEN_DB", "data/falsegreen.db"))

# A repo's score only moves when its tests change, so a short cache keeps the
# front page fast and stops a shared link re-cloning on every view.
CACHE_TTL_SECONDS = 60 * 60 * 6


SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    slug        TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    repo        TEXT NOT NULL,
    score       INTEGER NOT NULL,
    grade       TEXT NOT NULL,
    payload     TEXT NOT NULL,
    scanned_at  INTEGER NOT NULL,
    view_count  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_scans_recent ON scans(scanned_at DESC);
CREATE INDEX IF NOT EXISTS idx_scans_score  ON scans(score ASC);

CREATE TABLE IF NOT EXISTS waitlist (
    email       TEXT PRIMARY KEY,
    repo_slug   TEXT,
    plan        TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS hits (
    ip          TEXT NOT NULL,
    at          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hits ON hits(ip, at);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ----------------------------------------------------------------------
# Scans
# ----------------------------------------------------------------------


def save_scan(slug: str, owner: str, repo: str, score: int, grade: str, payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO scans (slug, owner, repo, score, grade, payload, scanned_at, view_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(slug) DO UPDATE SET
                score      = excluded.score,
                grade      = excluded.grade,
                payload    = excluded.payload,
                scanned_at = excluded.scanned_at
            """,
            (slug, owner, repo, score, grade, json.dumps(payload), int(time.time())),
        )


def get_scan(slug: str, max_age: int = CACHE_TTL_SECONDS) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM scans WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    if max_age and (time.time() - row["scanned_at"]) > max_age:
        return None
    return {**dict(row), "payload": json.loads(row["payload"])}


def touch_view(slug: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE scans SET view_count = view_count + 1 WHERE slug = ?", (slug,))


def recent_scans(limit: int = 12) -> list:
    with connect() as conn:
        rows = conn.execute(
            "SELECT slug, owner, repo, score, grade, scanned_at FROM scans "
            "ORDER BY scanned_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS repos, AVG(score) AS avg_score, MIN(score) AS worst FROM scans"
        ).fetchone()
    return {
        "repos": row["repos"] or 0,
        "avg_score": round(row["avg_score"]) if row["avg_score"] is not None else None,
        "worst": row["worst"],
    }


# ----------------------------------------------------------------------
# Waitlist
# ----------------------------------------------------------------------


def add_to_waitlist(email: str, repo_slug: str = "", plan: str = "team") -> bool:
    """Returns True if this is a new signup."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO waitlist (email, repo_slug, plan, created_at) VALUES (?, ?, ?, ?)",
            (email.strip().lower(), repo_slug, plan, int(time.time())),
        )
        return cur.rowcount > 0


def waitlist_size() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM waitlist").fetchone()["n"]


# ----------------------------------------------------------------------
# Rate limiting
# ----------------------------------------------------------------------


def rate_limited(ip: str, limit: int, window_seconds: int) -> bool:
    """True when this IP has exceeded `limit` scans in the window.

    Scanning clones a repo, so it is the expensive endpoint and the one worth
    protecting. Cheap and good enough at this scale; swap for Redis if the
    service ever runs on more than one process.
    """
    now = int(time.time())
    cutoff = now - window_seconds
    with connect() as conn:
        conn.execute("DELETE FROM hits WHERE at < ?", (cutoff,))
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM hits WHERE ip = ? AND at >= ?", (ip, cutoff)
        ).fetchone()["n"]
        if count >= limit:
            return True
        conn.execute("INSERT INTO hits (ip, at) VALUES (?, ?)", (ip, now))
        return False
