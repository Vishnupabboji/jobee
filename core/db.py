"""
SQLite database manager for Jobee.
WAL mode, 3 tables: jobs, scrape_runs, settings.
All datetime values stored as ISO-8601 UTC strings.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from core.models import JobPosting, ScrapeRun

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "jobs.db"

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_hash            TEXT    UNIQUE NOT NULL,
    normalized_company  TEXT    NOT NULL,
    normalized_title    TEXT    NOT NULL,
    title               TEXT,
    company             TEXT,
    location            TEXT,
    source              TEXT,
    apply_type          TEXT,
    apply_url           TEXT,
    contact_info        TEXT,
    posted_at_utc       TEXT,
    first_seen_utc      TEXT,
    last_seen_utc       TEXT,
    seen_count          INTEGER DEFAULT 1,
    is_repost           INTEGER DEFAULT 0,
    jd_text             TEXT,
    status              TEXT    DEFAULT 'To Apply',
    resume_mode_used    TEXT    DEFAULT 'single_base',
    resume_drive_url    TEXT,
    synced_to_sheet     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT,
    finished_at         TEXT,
    source              TEXT,
    status              TEXT,
    raw_count           INTEGER DEFAULT 0,
    dropped_old_count   INTEGER DEFAULT 0,
    filtered_out_count  INTEGER DEFAULT 0,
    duplicate_count     INTEGER DEFAULT 0,
    new_count           INTEGER DEFAULT 0,
    error_message       TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _ensure_data_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    _ensure_data_dir()
    con = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with _conn() as con:
        con.executescript(_DDL)
    log.info("Database initialised at %s", DB_PATH)


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def upsert_job(job: JobPosting) -> tuple[bool, bool]:
    """
    Insert or update a job.
    Returns (is_new, is_repost):
      - is_new=True  → freshly inserted
      - is_repost=True → hash already existed; seen_count incremented
    """
    now = _now_utc_iso()

    with _conn() as con:
        existing = con.execute(
            "SELECT id, seen_count FROM jobs WHERE job_hash = ?", (job.job_hash,)
        ).fetchone()

        if existing is None:
            con.execute(
                """
                INSERT INTO jobs (
                    job_hash, normalized_company, normalized_title,
                    title, company, location, source, apply_type,
                    apply_url, contact_info, posted_at_utc,
                    first_seen_utc, last_seen_utc, seen_count, is_repost,
                    jd_text, status, resume_mode_used, resume_drive_url,
                    synced_to_sheet
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job.job_hash, job.normalized_company, job.normalized_title,
                    job.title, job.company, job.location, job.source, job.apply_type.value,
                    job.apply_url, job.contact_info, _dt_to_iso(job.posted_at_utc),
                    now, now, 1, 0,
                    job.jd_text, job.status.value, job.resume_mode_used, job.resume_drive_url,
                    0
                )
            )
            return True, False
        else:
            new_count = existing["seen_count"] + 1
            con.execute(
                """
                UPDATE jobs
                SET seen_count=?, is_repost=1, last_seen_utc=?
                WHERE job_hash=?
                """,
                (new_count, now, job.job_hash)
            )
            return False, True


def get_all_jobs(
    status: Optional[str] = None,
    source: Optional[str] = None,
    role: Optional[str] = None,
    location: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 500
) -> list[dict[str, Any]]:
    """Fetch jobs with optional filters. Supports choosing role, location, both, or freeform keywords."""
    clauses: list[str] = ["is_repost = 0"]
    params: list[Any] = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if source:
        clauses.append("source = ?")
        params.append(source)
    if role:
        clauses.append("(title LIKE ? OR normalized_title LIKE ?)")
        params += [f"%{role}%", f"%{role}%"]
    if location:
        clauses.append("location LIKE ?")
        params.append(f"%{location}%")
    if keyword:
        clauses.append("(title LIKE ? OR company LIKE ? OR location LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]

    where = " AND ".join(clauses)
    sql = f"SELECT * FROM jobs WHERE {where} ORDER BY posted_at_utc DESC LIMIT ?"
    params.append(limit)

    with _conn() as con:
        rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_job_jd(job_hash: str, jd_text: str) -> bool:
    """Update job description text for a specific job."""
    with _conn() as con:
        cur = con.execute(
            "UPDATE jobs SET jd_text=? WHERE job_hash=?", (jd_text, job_hash)
        )
    return cur.rowcount > 0


def update_job_status(job_hash: str, status: str) -> bool:
    """Update status for a single job. Returns True if row was found."""
    with _conn() as con:
        cur = con.execute(
            "UPDATE jobs SET status=? WHERE job_hash=?", (status, job_hash)
        )
    return cur.rowcount > 0


def mark_synced_to_sheet(job_hashes: list[str]) -> None:
    with _conn() as con:
        con.executemany(
            "UPDATE jobs SET synced_to_sheet=1 WHERE job_hash=?",
            [(h,) for h in job_hashes]
        )


def get_unsynced_jobs() -> list[dict[str, Any]]:
    """Jobs not yet pushed to Google Sheets (and not reposts)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM jobs WHERE synced_to_sheet=0 AND is_repost=0"
        ).fetchall()
    return [dict(r) for r in rows]


def get_job_by_hash(job_hash: str) -> Optional[dict[str, Any]]:
    with _conn() as con:
        row = con.execute("SELECT * FROM jobs WHERE job_hash=?", (job_hash,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# ScrapeRun CRUD
# ---------------------------------------------------------------------------

def insert_scrape_run(run: ScrapeRun) -> int:
    with _conn() as con:
        cur = con.execute(
            """
            INSERT INTO scrape_runs (
                started_at, finished_at, source, status,
                raw_count, dropped_old_count, filtered_out_count,
                duplicate_count, new_count, error_message
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _dt_to_iso(run.started_at), _dt_to_iso(run.finished_at),
                run.source, run.status.value,
                run.raw_count, run.dropped_old_count, run.filtered_out_count,
                run.duplicate_count, run.new_count, run.error_message
            )
        )
    return cur.lastrowid


def get_scrape_runs(limit: int = 100) -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Settings CRUD
# ---------------------------------------------------------------------------

def get_setting(key: str, default: Any = None) -> Any:
    with _conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    serialised = json.dumps(value)
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            (key, serialised)
        )
