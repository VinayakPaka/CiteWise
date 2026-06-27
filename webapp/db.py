"""Local persistence for CiteWise's web app: accounts & history.

A tiny SQLite layer (Python's stdlib ``sqlite3`` — no extra dependency) that
stores the two things the web UI needs but the LangGraph pipeline does not:

  * **users**      — one row per account (email + password, or guest).
  * **researches** — every research run a user starts, so it shows up in their
                     history sidebar and can be re-opened later.

Login state itself is stateless — a signed JWT in an httponly cookie (see
``webapp/auth.py``) — so there is no sessions table to keep.

SQLite is deliberate: the demo stays fully self-contained (one ``citewise.db``
file, no server to run, no account to create). Connections are opened per call
— cheap for SQLite and naturally safe across FastAPI's threadpool workers.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    provider      TEXT    NOT NULL,            -- 'email' | 'guest'
    subject       TEXT    NOT NULL,            -- email address, or guest name slug
    email         TEXT,
    name          TEXT,
    picture       TEXT,
    password_hash TEXT,                        -- PBKDF2 hex digest (email accounts)
    password_salt TEXT,                        -- per-user salt (email accounts)
    created_at    REAL    NOT NULL,
    UNIQUE(provider, subject)
);

CREATE TABLE IF NOT EXISTS researches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id     TEXT,
    question      TEXT    NOT NULL,
    status        TEXT    NOT NULL,          -- 'draft' | 'exported' | 'refused'
    report_json   TEXT,
    verdicts_json TEXT,
    n_claims      INTEGER DEFAULT 0,
    n_verified    INTEGER DEFAULT 0,
    provider      TEXT,
    model         TEXT,
    created_at    REAL    NOT NULL,
    updated_at    REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_researches_user
    ON researches(user_id, created_at DESC);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables on first run; add new columns to an older DB (idempotent)."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # Databases created before email login lack the password columns — add
        # them so an existing citewise.db keeps working after upgrading.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        for col in ("password_hash", "password_salt"):
            if col not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def upsert_user(
    provider: str,
    subject: str,
    *,
    email: Optional[str] = None,
    name: Optional[str] = None,
    picture: Optional[str] = None,
) -> dict[str, Any]:
    """Find-or-create a user by (provider, subject); refresh their profile.

    Used for guest accounts; email accounts go through ``create_email_user``.
    """
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO users (provider, subject, email, name, picture, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider, subject) DO UPDATE SET
                    email   = COALESCE(excluded.email, users.email),
                    name    = COALESCE(excluded.name, users.name),
                    picture = COALESCE(excluded.picture, users.picture)""",
            (provider, subject, email, name, picture, now),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE provider = ? AND subject = ?",
            (provider, subject),
        ).fetchone()
    return dict(row)


def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    """Load a user row by primary key (used to resolve a JWT's subject)."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    """Find an email account by address (caller normalises to lower-case)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE provider = 'email' AND email = ?", (email,)
        ).fetchone()
    return dict(row) if row else None


def create_email_user(
    email: str, name: str, password_hash: str, password_salt: str
) -> dict[str, Any]:
    """Insert a new email account and return the created row."""
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO users
                    (provider, subject, email, name, password_hash, password_salt, created_at)
                    VALUES ('email', ?, ?, ?, ?, ?, ?)""",
            (email, email, name, password_hash, password_salt, now),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


# --------------------------------------------------------------------------- #
# Research history
# --------------------------------------------------------------------------- #
def save_research(
    user_id: int,
    thread_id: str,
    question: str,
    status: str,
    *,
    report: Optional[dict] = None,
    verdicts: Optional[list] = None,
    n_claims: int = 0,
    n_verified: int = 0,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> int:
    """Upsert a research run keyed by (user_id, thread_id); return its row id.

    Called when a draft is produced and again when it is approved/revised, so a
    single run keeps one history row that updates in place.
    """
    now = time.time()
    report_json = json.dumps(report) if report is not None else None
    verdicts_json = json.dumps(verdicts) if verdicts is not None else None
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM researches WHERE user_id = ? AND thread_id = ?",
            (user_id, thread_id),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE researches SET
                        question = ?, status = ?,
                        report_json = COALESCE(?, report_json),
                        verdicts_json = COALESCE(?, verdicts_json),
                        n_claims = ?, n_verified = ?, updated_at = ?
                    WHERE id = ?""",
                (question, status, report_json, verdicts_json,
                 n_claims, n_verified, now, existing["id"]),
            )
            return int(existing["id"])
        cur = conn.execute(
            """INSERT INTO researches
                    (user_id, thread_id, question, status, report_json, verdicts_json,
                     n_claims, n_verified, provider, model, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, thread_id, question, status, report_json, verdicts_json,
             n_claims, n_verified, provider, model, now, now),
        )
        return int(cur.lastrowid)


def list_researches(user_id: int) -> list[dict[str, Any]]:
    """Lightweight list for the history sidebar (no heavy JSON payloads)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, thread_id, question, status, n_claims, n_verified,
                      created_at, updated_at
                 FROM researches WHERE user_id = ?
                ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_research(user_id: int, research_id: int) -> Optional[dict[str, Any]]:
    """Full record (parsed report + verdicts) for re-opening from history."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM researches WHERE id = ? AND user_id = ?",
            (research_id, user_id),
        ).fetchone()
    if not row:
        return None
    rec = dict(row)
    rec["report"] = json.loads(rec.pop("report_json")) if rec.get("report_json") else None
    rec["verdicts"] = json.loads(rec.pop("verdicts_json")) if rec.get("verdicts_json") else []
    return rec


def delete_research(user_id: int, research_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM researches WHERE id = ? AND user_id = ?",
            (research_id, user_id),
        )
        return cur.rowcount > 0
