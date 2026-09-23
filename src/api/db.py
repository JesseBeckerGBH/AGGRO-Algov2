# src/api/db.py
"""Lightweight job-queue database for the AGGRO API layer.

This module owns EXACTLY ONE concern: tracking async mission-run lifecycle
(queued → running → done | failed).  Mission content, brief bodies, evidence,
and source records all live in research-memory.sqlite managed by
research_os.memory.Memory — this module never duplicates that data.

Connection model
----------------
SQLite with check_same_thread=False is safe here because FastAPI serialises
writes through BackgroundTasks on a single worker thread.  Each request
function that needs the DB calls get_job_db() which yields a fresh connection
and closes it on teardown — identical to the pattern Memory uses.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

_DB_PATH = Path("aggro-jobs.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id   TEXT    NOT NULL,          -- matches missions.mission_id in research-memory.sqlite
    status       TEXT    NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
    connector    TEXT    NOT NULL DEFAULT 'brave',
    queued_at    TEXT    NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    error        TEXT                        -- populated only on failure
);
CREATE INDEX IF NOT EXISTS ix_jobs_mission ON jobs(mission_id);
CREATE INDEX IF NOT EXISTS ix_jobs_status  ON jobs(status);
"""


@contextmanager
def get_job_db():
    """Context-manager dependency: open, yield, close.

    Usage in a FastAPI route::

        with get_job_db() as db:
            job = create_job(db, ...)
    """
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_job(db: sqlite3.Connection, *, mission_id: str,
               connector: str, queued_at: str) -> sqlite3.Row:
    cur = db.execute(
        "INSERT INTO jobs (mission_id, connector, status, queued_at) "
        "VALUES (?, ?, 'queued', ?)",
        (mission_id, connector, queued_at),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM jobs WHERE job_id = ?", (cur.lastrowid,)
    ).fetchone()


def set_job_running(db: sqlite3.Connection, job_id: int, started_at: str) -> None:
    db.execute(
        "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
        (started_at, job_id),
    )
    db.commit()


def set_job_done(db: sqlite3.Connection, job_id: int, finished_at: str) -> None:
    db.execute(
        "UPDATE jobs SET status = 'done', finished_at = ? WHERE job_id = ?",
        (finished_at, job_id),
    )
    db.commit()


def set_job_failed(db: sqlite3.Connection, job_id: int,
                   finished_at: str, error: str) -> None:
    db.execute(
        "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE job_id = ?",
        (finished_at, error[:2000], job_id),
    )
    db.commit()


def get_job(db: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()


def list_jobs(db: sqlite3.Connection, mission_id: str | None = None,
              limit: int = 50) -> list[sqlite3.Row]:
    if mission_id:
        return list(db.execute(
            "SELECT * FROM jobs WHERE mission_id = ? ORDER BY job_id DESC LIMIT ?",
            (mission_id, limit),
        ))
    return list(db.execute(
        "SELECT * FROM jobs ORDER BY job_id DESC LIMIT ?", (limit,)
    ))
