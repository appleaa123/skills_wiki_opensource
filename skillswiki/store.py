"""Local SQLite store (stdlib sqlite3). One connection per call; the schema is created on first open.

Timestamps are UTC ISO-8601 strings so `>= since_iso` string comparisons work. JSON columns are TEXT.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from skillswiki import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
  slug TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('adopted','native','plugin')),
  path TEXT NOT NULL,
  origin_path TEXT,
  fingerprint TEXT,
  has_scripts INTEGER NOT NULL DEFAULT 0,
  context_tokens_est INTEGER NOT NULL DEFAULT 0,
  adopted_at TEXT, updated_at TEXT NOT NULL,
  copies TEXT);                  -- JSON [{"origin", "stored"}]: identical copies moved along with the skill
CREATE TABLE IF NOT EXISTS cards (
  slug TEXT PRIMARY KEY, examples TEXT NOT NULL DEFAULT '[]', keywords TEXT NOT NULL DEFAULT '[]',
  not_for TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL CHECK (source IN ('enrich','manual')),
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learnings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL, body TEXT NOT NULL,
  superseded_by INTEGER, retired_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS routing_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_excerpt TEXT NOT NULL, mode TEXT NOT NULL,
  suggested TEXT, shortlist TEXT NOT NULL DEFAULT '[]', confidence REAL, reason TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL,
  event TEXT NOT NULL CHECK (event IN ('load','suggest','eval')),
  tokens_est INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL, tier TEXT, result_path TEXT NOT NULL,
  verdict TEXT, delta_pp REAL, rubric_score REAL, tokens_total INTEGER, accepted INTEGER NOT NULL DEFAULT 0,
  results TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wiring (
  agent TEXT PRIMARY KEY,
  method TEXT NOT NULL CHECK (method IN ('hook','rules','advice')),
  self_setup INTEGER NOT NULL DEFAULT 0,          -- 1: the agent edits its own config (D31)
  mcp TEXT NOT NULL DEFAULT 'none' CHECK (mcp IN ('none','done','present','self_setup')),
  test TEXT NOT NULL CHECK (test IN ('tested_ok','untested','failed')),
  updated_at TEXT NOT NULL,
  vouched INTEGER NOT NULL DEFAULT 0);             -- 1: the user skipped the test because we tested this agent
CREATE TABLE IF NOT EXISTS wiring_files (
  path TEXT PRIMARY KEY, created INTEGER NOT NULL,  -- 1: the file did not exist before setup
  original TEXT,                                    -- copy of the pre-setup bytes (NULL when created)
  written_sha TEXT NOT NULL,                        -- sha256 of what we last wrote
  parts TEXT NOT NULL DEFAULT '[]',                 -- JSON [{"user": "<agent>:<slot>", "part": "<id>"}]
  made_dirs TEXT NOT NULL DEFAULT '[]');            -- JSON folders setup created for this file, deepest first
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was created."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(skills)")}
    if "copies" not in columns:
        conn.execute("ALTER TABLE skills ADD COLUMN copies TEXT")
    wiring_columns = {r["name"] for r in conn.execute("PRAGMA table_info(wiring)")}
    if "vouched" not in wiring_columns:  # databases created by v0.3.0
        conn.execute("ALTER TABLE wiring ADD COLUMN vouched INTEGER NOT NULL DEFAULT 0")
    file_columns = {r["name"] for r in conn.execute("PRAGMA table_info(wiring_files)")}
    if "made_dirs" not in file_columns:  # databases created by v0.3.0 / v0.3.1
        conn.execute("ALTER TABLE wiring_files ADD COLUMN made_dirs TEXT NOT NULL DEFAULT '[]'")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open the database, ensure the schema, commit on success, roll back on error, always close."""
    conn = sqlite3.connect(paths.db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_setting(key: str, default: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
