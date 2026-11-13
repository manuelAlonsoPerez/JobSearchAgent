"""SQLite persistence for job deduplication, results, and review items."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .config import get_settings, site_id_for_name
from .models import JobState
from .utils.text import (
    canonical_apply_url,
    canonicalize_url,
    content_fingerprint,
    extract_linkedin_job_id,
    job_dedup_key,
)

REVIEW_STATUSES = frozenset({"pending", "applied", "rejected", "done"})
_JOB_STATUS_FOR_REVIEW = {
    "pending": "manual",
    "applied": "applied",
    "rejected": "rejected",
    "done": "applied",
}


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
                employment_type TEXT,
                site_id TEXT,
                content_fingerprint TEXT
            );

            CREATE INDEX IF NOT EXISTS jobs_site_source_id
                ON jobs(site, source_job_id);

            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                reason TEXT NOT NULL,
                link TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                profile_id TEXT,
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
        if "site_id" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN site_id TEXT")
        if "content_fingerprint" not in columns:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN content_fingerprint TEXT"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jobs_content_fingerprint
                ON jobs(content_fingerprint)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS jobs_site_id_source
                ON jobs(site_id, source_job_id)
            """
        )
        _backfill_site_ids(connection)
        review_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(review_queue)")
        }
        if "profile_id" not in review_columns:
            connection.execute(
                "ALTER TABLE review_queue ADD COLUMN profile_id TEXT"
            )
        connection.execute(
            """
            UPDATE review_queue
            SET profile_id = (
                SELECT jobs.profile_id
                FROM jobs
                WHERE jobs.url = review_queue.url
                  AND jobs.profile_id IS NOT NULL
                  AND jobs.profile_id <> ''
                LIMIT 1
            )
            WHERE profile_id IS NULL
               OR profile_id = ''
            """
        )


def _backfill_site_ids(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT dedup_key, site
        FROM jobs
        WHERE site_id IS NULL OR site_id = ''
        """
    ).fetchall()
    for row in rows:
        site_id = site_id_for_name(str(row["site"] or ""))
        if not site_id:
            continue
        connection.execute(
            "UPDATE jobs SET site_id = ? WHERE dedup_key = ?",
            (site_id, row["dedup_key"]),
        )


def _job_identity(
    *,
    site: str,
    source_job_id: str,
    url: str,
    site_id: str | None,
    apply_url: str | None,
    company: str | None,
    title: str | None,
    location: str | None,
) -> dict[str, str]:
    canonical_url = canonicalize_url(url) if str(url or "").strip() else ""
    resolved_site_id = str(site_id or "").strip() or site_id_for_name(site) or ""
    stored_apply = canonical_apply_url(apply_url) or str(apply_url or "").strip()
    return {
        "dedup_key": (
            job_dedup_key(site, canonical_url or url, source_job_id or None)
            if site and (canonical_url or url)
            else ""
        ),
        "site": site,
        "site_id": resolved_site_id,
        "source_job_id": str(source_job_id or "").strip(),
        "url": canonical_url,
        "apply_url": canonical_apply_url(apply_url) or "",
        "stored_apply_url": stored_apply,
        "content_fingerprint": content_fingerprint(company, title, location) or "",
    }


def _other_row_owns_job(
    connection: sqlite3.Connection,
    identity: dict[str, str],
) -> bool:
    """True when a different saved row already owns this listing."""

    row = connection.execute(
        """
        SELECT 1
        FROM jobs
        WHERE dedup_key <> ?
          AND (
            (
                source_job_id <> ''
                AND source_job_id = ?
                AND (site = ? OR (? <> '' AND site_id = ?))
            )
            OR (? <> '' AND url = ?)
            OR (? <> '' AND apply_url = ?)
            OR (? <> '' AND content_fingerprint = ?)
          )
        LIMIT 1
        """,
        (
            identity["dedup_key"],
            identity["source_job_id"],
            identity["site"],
            identity["site_id"],
            identity["site_id"],
            identity["url"],
            identity["url"],
            identity["apply_url"],
            identity["apply_url"],
            identity["content_fingerprint"],
            identity["content_fingerprint"],
        ),
    ).fetchone()
    return row is not None


def seen(
    site: str,
    source_job_id: str,
    url: str,
    *,
    path: str | Path | None = None,
    site_id: str | None = None,
    apply_url: str | None = None,
    company: str | None = None,
    title: str | None = None,
    location: str | None = None,
) -> bool:
    """Return whether this listing was already saved on any board.

    A match is the same source id on one site, the same canonical listing
    URL, the same absolute apply URL, or the same company, title, and location.
    """

    init_db(path)
    identity = _job_identity(
        site=site,
        source_job_id=source_job_id,
        url=url,
        site_id=site_id,
        apply_url=apply_url,
        company=company,
        title=title,
        location=location,
    )
    with _connection(path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM jobs
            WHERE (? <> '' AND dedup_key = ?)
               OR (
                    source_job_id <> ''
                    AND source_job_id = ?
                    AND (site = ? OR (? <> '' AND site_id = ?))
               )
               OR (? <> '' AND url = ?)
               OR (? <> '' AND apply_url = ?)
               OR (? <> '' AND content_fingerprint = ?)
            LIMIT 1
            """,
            (
                identity["dedup_key"],
                identity["dedup_key"],
                identity["source_job_id"],
                identity["site"],
                identity["site_id"],
                identity["site_id"],
                identity["url"],
                identity["url"],
                identity["apply_url"],
                identity["apply_url"],
                identity["content_fingerprint"],
                identity["content_fingerprint"],
            ),
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

    init_db(path)
    identity = _job_identity(
        site=site,
        source_job_id=source_job_id,
        url=raw_url,
        site_id=str(state.get("site_id") or "") or None,
        apply_url=state.get("apply_url"),
        company=state.get("company"),
        title=state.get("title"),
        location=state.get("location"),
    )
    values: dict[str, Any] = {
        "dedup_key": identity["dedup_key"],
        "site": identity["site"],
        "site_id": identity["site_id"] or None,
        "source_job_id": identity["source_job_id"],
        "url": identity["url"],
        "content_fingerprint": identity["content_fingerprint"] or None,
        "title": state.get("title"),
        "company": state.get("company"),
        "location": state.get("location"),
        "raw_description": state.get("raw_description"),
        "easy_apply": int(bool(state.get("easy_apply"))),
        "apply_url": identity["stored_apply_url"] or None,
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
        if _other_row_owns_job(connection, identity):
            return
        connection.execute(
            """
            INSERT INTO jobs (
                dedup_key, site, site_id, source_job_id, url, title, company,
                location, raw_description, easy_apply, apply_url, profile_id,
                outcome, fit_score, review_reason, apply_status, attempts,
                applied_at, employment_type, location_eligibility,
                skills_outcome, content_fingerprint
            ) VALUES (
                :dedup_key, :site, :site_id, :source_job_id, :url, :title,
                :company, :location, :raw_description, :easy_apply, :apply_url,
                :profile_id, :outcome, :fit_score, :review_reason,
                :apply_status, :attempts,
                CASE WHEN :apply_status = 'applied' THEN CURRENT_TIMESTAMP END,
                :employment_type, :location_eligibility, :skills_outcome,
                :content_fingerprint
            )
            ON CONFLICT(dedup_key) DO UPDATE SET
                site_id = COALESCE(jobs.site_id, excluded.site_id),
                content_fingerprint = COALESCE(
                    excluded.content_fingerprint, jobs.content_fingerprint
                ),
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
    values.pop("content_fingerprint", None)
    values["easy_apply"] = bool(values.get("easy_apply"))
    return JobState(**values)


def add_to_review(
    url: str,
    reason: str,
    link: str | None = None,
    profile_id: str | None = None,
    *,
    path: str | Path | None = None,
) -> None:
    """Append a non-blocking review item."""

    init_db(path)
    canonical = canonicalize_url(url)
    stored_profile = str(profile_id or "").strip() or None
    with _connection(path) as connection:
        if stored_profile is None:
            row = connection.execute(
                """
                SELECT profile_id
                FROM jobs
                WHERE url = ?
                  AND profile_id IS NOT NULL
                  AND profile_id <> ''
                LIMIT 1
                """,
                (canonical,),
            ).fetchone()
            if row is not None:
                stored_profile = str(row["profile_id"])
        connection.execute(
            """
            INSERT INTO review_queue(url, reason, link, profile_id)
            VALUES (?, ?, ?, ?)
            """,
            (canonical, reason, link, stored_profile),
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
    if status in {"applied", "done"}:
        clauses.append("rq.status IN (?, ?)")
        params.extend(["applied", "done"])
    elif status:
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
            COALESCE(rq.profile_id, j.profile_id) AS profile_id,
            j.dedup_key AS dedup_key,
            j.source_job_id AS source_job_id,
            j.title AS title,
            j.company AS company,
            j.location AS location,
            j.outcome AS outcome,
            j.fit_score AS fit_score,
            j.apply_status AS apply_status,
            j.easy_apply AS easy_apply,
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
    """Mark a review item applied, rejected, or pending, and sync the job."""

    if status not in REVIEW_STATUSES:
        raise ValueError(
            "Review status must be 'pending', 'applied', or 'rejected'"
        )

    stored = "applied" if status == "done" else status
    job_status = _JOB_STATUS_FOR_REVIEW[status]
    init_db(path)
    with _connection(path) as connection:
        row = connection.execute(
            "SELECT url, link FROM review_queue WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE review_queue SET status = ? WHERE id = ?",
            (stored, item_id),
        )
        urls = [value for value in (row["url"], row["link"]) if value]
        if urls:
            placeholders = ", ".join("?" for _ in urls)
            connection.execute(
                f"""
                UPDATE jobs
                SET apply_status = ?,
                    updated_at = CURRENT_TIMESTAMP,
                    applied_at = CASE
                        WHEN ? = 'applied' THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END
                WHERE url IN ({placeholders})
                   OR apply_url IN ({placeholders})
                """,
                (job_status, job_status, *urls, *urls),
            )
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
        "review_applied": int(review.get("applied", 0) + review.get("done", 0)),
        "review_rejected": int(review.get("rejected", 0)),
        "review_done": int(
            review.get("done", 0)
            + review.get("applied", 0)
            + review.get("rejected", 0)
        ),
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
    values.pop("content_fingerprint", None)
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
