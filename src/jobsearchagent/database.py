"""SQLite persistence for job deduplication, results, and review items."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .config import get_settings
from .models import JobState
from .utils.text import (
    canonicalize_url,
    extract_linkedin_job_id,
    job_dedup_key,
)


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
                applied_at TEXT,
                employment_type TEXT
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
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(jobs)")
        }
        if "employment_type" not in columns:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN employment_type TEXT"
            )
        if "location_eligibility" not in columns:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN location_eligibility TEXT"
            )
        if "non_location_outcome" in columns and "skills_outcome" not in columns:
            connection.execute(
                "ALTER TABLE jobs RENAME COLUMN non_location_outcome "
                "TO skills_outcome"
            )
            columns.discard("non_location_outcome")
            columns.add("skills_outcome")
        if "skills_outcome" not in columns:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN skills_outcome TEXT"
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
        "employment_type": state.get("employment_type"),
        "location_eligibility": _json_text(state.get("location_eligibility")),
        "skills_outcome": _json_text(
            state.get("skills_outcome") or state.get("non_location_outcome")
        ),
    }
    with _connection(path) as connection:
        connection.execute(
            """
            INSERT INTO jobs (
                dedup_key, site, source_job_id, url, title, company, location,
                raw_description, easy_apply, apply_url, profile_id, outcome,
                fit_score, review_reason, apply_status, attempts, applied_at,
                employment_type, location_eligibility, skills_outcome
            ) VALUES (
                :dedup_key, :site, :source_job_id, :url, :title, :company,
                :location, :raw_description, :easy_apply, :apply_url,
                :profile_id, :outcome, :fit_score, :review_reason,
                :apply_status, :attempts,
                CASE WHEN :apply_status = 'applied' THEN CURRENT_TIMESTAMP END,
                :employment_type, :location_eligibility, :skills_outcome
            )
            ON CONFLICT(dedup_key) DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                location = excluded.location,
                employment_type = COALESCE(
                    excluded.employment_type, jobs.employment_type
                ),
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
                location_eligibility = COALESCE(
                    excluded.location_eligibility, jobs.location_eligibility
                ),
                skills_outcome = COALESCE(
                    excluded.skills_outcome, jobs.skills_outcome
                ),
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


def get_job(
    identifier: str,
    *,
    path: str | Path | None = None,
) -> JobState | None:
    """Load a persisted job by LinkedIn ID or URL."""

    normalized = identifier.strip()
    if not normalized:
        raise ValueError("A job ID or URL is required")

    init_db(path)
    source_job_id = (
        normalized
        if normalized.isdigit()
        else extract_linkedin_job_id(normalized)
    )
    canonical_url = (
        canonicalize_url(normalized)
        if "://" in normalized
        else None
    )
    with _connection(path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE (? IS NOT NULL AND source_job_id = ?)
               OR (? IS NOT NULL AND url = ?)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (
                source_job_id,
                source_job_id,
                canonical_url,
                canonical_url,
            ),
        ).fetchone()
    if row is None:
        return None

    values = dict(row)
    values.pop("dedup_key", None)
    values.pop("discovered_at", None)
    values.pop("updated_at", None)
    values.pop("applied_at", None)
    values["easy_apply"] = bool(values.get("easy_apply"))
    return JobState(**values)


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


def list_jobs(
    *,
    path: str | Path | None = None,
    query: str = "",
    outcome: str | None = None,
    apply_status: str | None = None,
) -> list[dict[str, Any]]:
    """Return analyzed jobs for the local UI, newest first."""

    init_db(path)
    clauses = ["1 = 1"]
    params: list[Any] = []
    needle = query.strip().casefold()
    if needle:
        like = f"%{needle}%"
        clauses.append(
            """
            (
                lower(COALESCE(title, '')) LIKE ?
                OR lower(COALESCE(company, '')) LIKE ?
                OR lower(COALESCE(location, '')) LIKE ?
                OR lower(url) LIKE ?
            )
            """
        )
        params.extend([like, like, like, like])
    if outcome == "untriaged":
        clauses.append("outcome IS NULL")
    elif outcome:
        clauses.append("outcome = ?")
        params.append(outcome)
    if apply_status == "none":
        clauses.append("apply_status IS NULL")
    elif apply_status:
        clauses.append("apply_status = ?")
        params.append(apply_status)

    sql = f"""
        SELECT
            dedup_key, site, source_job_id, url, title, company, location,
            easy_apply, apply_url, profile_id, outcome, fit_score,
            review_reason, apply_status, attempts, discovered_at, updated_at,
            applied_at, employment_type
        FROM jobs
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, discovered_at DESC
    """
    with _connection(path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_job_public_dict(row, include_description=False) for row in rows]


def get_job_detail(
    identifier: str,
    *,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load one job for the UI by dedup key, LinkedIn ID, or URL."""

    normalized = identifier.strip()
    if not normalized:
        raise ValueError("A job identifier is required")

    init_db(path)
    canonical_url = (
        canonicalize_url(normalized) if "://" in normalized else normalized
    )
    with _connection(path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE dedup_key = ?
               OR source_job_id = ?
               OR url = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (normalized, normalized, canonical_url),
        ).fetchone()
    if row is None:
        return None
    return _job_public_dict(row, include_description=True)


def list_review_queue(
    *,
    path: str | Path | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Return review items joined with job fields when the URL matches."""

    init_db(path)
    clauses = ["1 = 1"]
    params: list[Any] = []
    if status:
        clauses.append("rq.status = ?")
        params.append(status)
    sql = f"""
        SELECT
            rq.id AS id,
            rq.url AS url,
            rq.reason AS reason,
            rq.link AS link,
            rq.status AS status,
            rq.created_at AS created_at,
            j.dedup_key AS dedup_key,
            j.source_job_id AS source_job_id,
            j.title AS title,
            j.company AS company,
            j.location AS location,
            j.outcome AS outcome,
            j.fit_score AS fit_score,
            j.apply_status AS apply_status,
            j.easy_apply AS easy_apply,
            j.profile_id AS profile_id,
            j.employment_type AS employment_type
        FROM review_queue AS rq
        LEFT JOIN jobs AS j ON j.url = rq.url
        WHERE {' AND '.join(clauses)}
        ORDER BY rq.created_at DESC, rq.id DESC
    """
    with _connection(path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_review_public_dict(row) for row in rows]


def set_review_status(
    item_id: int,
    status: str,
    *,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Mark a review-queue row pending or done. Returns the updated item."""

    if status not in {"pending", "done"}:
        raise ValueError("Review status must be 'pending' or 'done'")

    init_db(path)
    with _connection(path) as connection:
        connection.execute(
            "UPDATE review_queue SET status = ? WHERE id = ?",
            (status, item_id),
        )
        if connection.execute(
            "SELECT changes() AS changed"
        ).fetchone()["changed"] == 0:
            return None
    items = [
        item
        for item in list_review_queue(path=path)
        if item["id"] == item_id
    ]
    return items[0] if items else None


def summarize_jobs(*, path: str | Path | None = None) -> dict[str, Any]:
    """Counts for the local UI header."""

    init_db(path)
    with _connection(path) as connection:
        job_total = connection.execute(
            "SELECT COUNT(*) AS count FROM jobs"
        ).fetchone()["count"]
        outcomes = {
            str(row["key"]): int(row["total"])
            for row in connection.execute(
                """
                SELECT COALESCE(outcome, 'untriaged') AS key, COUNT(*) AS total
                FROM jobs
                GROUP BY outcome
                """
            )
        }
        statuses = {
            str(row["key"]): int(row["total"])
            for row in connection.execute(
                """
                SELECT COALESCE(apply_status, 'none') AS key, COUNT(*) AS total
                FROM jobs
                GROUP BY apply_status
                """
            )
        }
        review = {
            str(row["status"]): int(row["total"])
            for row in connection.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM review_queue
                GROUP BY status
                """
            )
        }
    return {
        "jobs": int(job_total),
        "outcomes": outcomes,
        "apply_statuses": statuses,
        "review_pending": int(review.get("pending", 0)),
        "review_done": int(review.get("done", 0)),
        "review_total": sum(review.values()),
    }


def _json_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _parse_json_field(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _job_public_dict(
    row: sqlite3.Row,
    *,
    include_description: bool,
) -> dict[str, Any]:
    values = dict(row)
    values["easy_apply"] = bool(values.get("easy_apply"))
    if "location_eligibility" in values:
        values["location_eligibility"] = _parse_json_field(
            values.get("location_eligibility")
        )
    if "skills_outcome" in values:
        values["skills_outcome"] = _parse_json_field(
            values.get("skills_outcome")
        )
    values.pop("non_location_outcome", None)
    if not include_description:
        values.pop("raw_description", None)
        values.pop("location_eligibility", None)
        values.pop("skills_outcome", None)
    return values


def _review_public_dict(row: sqlite3.Row) -> dict[str, Any]:
    values = dict(row)
    if values.get("easy_apply") is not None:
        values["easy_apply"] = bool(values["easy_apply"])
    return values


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
