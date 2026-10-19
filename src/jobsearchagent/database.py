"""SQLite persistence for job deduplication, results, and review items."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .config import get_settings
from .models import JobState
from .utils.text import canonicalize_url, job_dedup_key


def _database_path(path: str | Path | None = None) -> Path:
    database_path = Path(path or get_settings().db_path).expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return database_path


@contextmanager
def _connection(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(_database_path(path))
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db(path: str | Path | None = None) -> None:
    """Create the durable job and review-queue tables idempotently."""

    with _connection(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                dedup_key TEXT PRIMARY KEY,
                site TEXT NOT NULL,
                source_job_id TEXT,
                url TEXT NOT NULL,
                title TEXT,
                company TEXT,
                location TEXT,
                raw_description TEXT,
                easy_apply INTEGER NOT NULL DEFAULT 0,
                apply_url TEXT,
                profile_id TEXT,
                outcome TEXT,
                fit_score REAL,
                review_reason TEXT,
                apply_status TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                applied_at TEXT
            );

            CREATE INDEX IF NOT EXISTS jobs_site_source_id
                ON jobs(site, source_job_id);

            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                reason TEXT NOT NULL,
                link TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def seen(
    site: str,
    source_job_id: str,
    url: str,
    *,
    path: str | Path | None = None,
) -> bool:
    """Return whether the stable source ID or canonical URL was persisted."""

    init_db(path)
    key = job_dedup_key(site, url, source_job_id or None)
    canonical_url = canonicalize_url(url)
    with _connection(path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM jobs
            WHERE dedup_key = ?
               OR (site = ? AND source_job_id <> '' AND source_job_id = ?)
               OR (site = ? AND url = ?)
            LIMIT 1
            """,
            (key, site, source_job_id, site, canonical_url),
        ).fetchone()
    return row is not None


def upsert_job(
    state: JobState,
    *,
    path: str | Path | None = None,
) -> None:
    """Insert a discovered job or update its latest pipeline fields."""

    site = str(state.get("site") or "")
    raw_url = str(state.get("url") or "")
    source_job_id = str(state.get("source_job_id") or "")
    if not site or not raw_url:
        raise ValueError("A persisted job requires non-empty site and url fields")
    url = canonicalize_url(raw_url)

    init_db(path)
    key = job_dedup_key(site, url, source_job_id or None)
    values: dict[str, Any] = {
        "dedup_key": key,
        "site": site,
        "source_job_id": source_job_id,
        "url": url,
        "title": state.get("title"),
        "company": state.get("company"),
        "location": state.get("location"),
        "raw_description": state.get("raw_description"),
        "easy_apply": int(bool(state.get("easy_apply"))),
        "apply_url": state.get("apply_url"),
        "profile_id": state.get("profile_id"),
        "outcome": state.get("outcome"),
        "fit_score": state.get("fit_score"),
        "review_reason": state.get("review_reason"),
        "apply_status": state.get("apply_status"),
        "attempts": int(state.get("attempts", 0)),
    }
    with _connection(path) as connection:
        connection.execute(
            """
            INSERT INTO jobs (
                dedup_key, site, source_job_id, url, title, company, location,
                raw_description, easy_apply, apply_url, profile_id, outcome,
                fit_score, review_reason, apply_status, attempts, applied_at
            ) VALUES (
                :dedup_key, :site, :source_job_id, :url, :title, :company,
                :location, :raw_description, :easy_apply, :apply_url,
                :profile_id, :outcome, :fit_score, :review_reason,
                :apply_status, :attempts,
                CASE WHEN :apply_status = 'applied' THEN CURRENT_TIMESTAMP END
            )
            ON CONFLICT(dedup_key) DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                location = excluded.location,
                raw_description = excluded.raw_description,
                easy_apply = excluded.easy_apply,
                apply_url = COALESCE(excluded.apply_url, jobs.apply_url),
                profile_id = COALESCE(excluded.profile_id, jobs.profile_id),
                outcome = COALESCE(excluded.outcome, jobs.outcome),
                fit_score = COALESCE(excluded.fit_score, jobs.fit_score),
                review_reason = COALESCE(
                    excluded.review_reason, jobs.review_reason
                ),
                apply_status = COALESCE(excluded.apply_status, jobs.apply_status),
                attempts = excluded.attempts,
                updated_at = CURRENT_TIMESTAMP,
                applied_at = CASE
                    WHEN excluded.apply_status = 'applied'
                    THEN CURRENT_TIMESTAMP
                    ELSE jobs.applied_at
                END
            """,
            values,
        )


def record_result(
    state: JobState,
    *,
    path: str | Path | None = None,
) -> None:
    """Persist the latest result for a job."""

    upsert_job(state, path=path)


def add_to_review(
    url: str,
    reason: str,
    link: str | None = None,
    *,
    path: str | Path | None = None,
) -> None:
    """Append a non-blocking review item."""

    init_db(path)
    with _connection(path) as connection:
        connection.execute(
            """
            INSERT INTO review_queue(url, reason, link)
            VALUES (?, ?, ?)
            """,
            (canonicalize_url(url), reason, link),
        )


def count_applied_today(
    site: str,
    *,
    path: str | Path | None = None,
) -> int:
    """Count successful submissions recorded today for one site."""

    init_db(path)
    with _connection(path) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM jobs
            WHERE site = ?
              AND apply_status = 'applied'
              AND date(applied_at, 'localtime') = date('now', 'localtime')
            """,
            (site,),
        ).fetchone()
    return int(row["count"] if row else 0)


def get_checkpointer(
    path: str | Path | None = None,
) -> SqliteSaver:
    """Return a SQLite LangGraph checkpointer for thread-per-job workflows."""

    database_path = _database_path(path)
    connection = sqlite3.connect(database_path, check_same_thread=False)
    return SqliteSaver(connection)
