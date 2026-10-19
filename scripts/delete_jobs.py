#!/usr/bin/env python3
"""Delete JobSearchAgent job data while preserving the database schema."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from jobsearchagent.config import get_settings


def delete_jobs(
    database_path: str | Path,
    *,
    include_review_queue: bool = True,
) -> tuple[int, int]:
    """Delete stored jobs and optionally review items; return deleted counts."""

    path = Path(database_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Database file not found: {path}")

    with sqlite3.connect(path) as connection:
        job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        review_count = 0
        if include_review_queue:
            review_count = connection.execute(
                "SELECT COUNT(*) FROM review_queue"
            ).fetchone()[0]

        connection.execute("DELETE FROM jobs")
        if include_review_queue:
            connection.execute("DELETE FROM review_queue")

    return int(job_count), int(review_count)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Delete all stored jobs while preserving SQLite tables and schema."
        )
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override DB_PATH from .env.",
    )
    parser.add_argument(
        "--keep-review-queue",
        action="store_true",
        help="Delete jobs but preserve review_queue entries.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation.",
    )
    arguments = parser.parse_args()

    path = (arguments.db_path or get_settings().db_path).expanduser()
    if not path.is_file():
        parser.error(f"database file not found: {path}")

    with sqlite3.connect(path) as connection:
        jobs = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        reviews = connection.execute(
            "SELECT COUNT(*) FROM review_queue"
        ).fetchone()[0]

    print(f"Database: {path}")
    print(f"Jobs to delete: {jobs}")
    if arguments.keep_review_queue:
        print(f"Review items preserved: {reviews}")
    else:
        print(f"Review items to delete: {reviews}")

    if not arguments.yes:
        confirmation = input("Type DELETE to continue: ")
        if confirmation.strip() != "DELETE":
            print("Cancelled; no data was deleted.")
            return

    deleted_jobs, deleted_reviews = delete_jobs(
        path,
        include_review_queue=not arguments.keep_review_queue,
    )
    print(f"Deleted {deleted_jobs} jobs and {deleted_reviews} review items.")


if __name__ == "__main__":
    main()
