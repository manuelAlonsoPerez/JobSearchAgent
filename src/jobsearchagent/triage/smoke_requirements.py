"""Manual smoke runner for the artifact-requirements stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..database import get_job
from ..models import JobState
from .requirements import decide_requirements


def evaluate_persisted_job(
    identifier: str,
    *,
    database_path: str | Path | None = None,
    force_accepted_easy_apply: bool = False,
) -> dict[str, Any]:
    """Load the previous stage's state and evaluate its artifact requirements."""

    state = get_job(identifier, path=database_path)
    if state is None:
        raise LookupError(
            f"No persisted job matches {identifier!r}. Run LinkedIn discovery "
            "and triage first."
        )

    effective_state = JobState(**state)
    if force_accepted_easy_apply:
        effective_state["outcome"] = "accept"
        effective_state["easy_apply"] = True

    update = decide_requirements(effective_state)
    return {
        "job": {
            "source_job_id": effective_state.get("source_job_id"),
            "title": effective_state.get("title"),
            "company": effective_state.get("company"),
            "url": effective_state.get("url"),
        },
        "previous_stage": {
            "profile_id": state.get("profile_id"),
            "outcome": state.get("outcome"),
            "fit_score": state.get("fit_score"),
            "easy_apply": state.get("easy_apply"),
        },
        "smoke_override_applied": force_accepted_easy_apply,
        **update,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Load one persisted LinkedIn job and manually test the artifact-"
            "requirements stage."
        )
    )
    parser.add_argument(
        "job",
        help="LinkedIn job ID or full LinkedIn job URL.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override DB_PATH from .env.",
    )
    parser.add_argument(
        "--force-accepted-easy-apply",
        action="store_true",
        help=(
            "Smoke-test the accepted Easy Apply branch without changing the "
            "persisted triage result."
        ),
    )
    arguments = parser.parse_args()

    try:
        result = evaluate_persisted_job(
            arguments.job,
            database_path=arguments.db_path,
            force_accepted_easy_apply=arguments.force_accepted_easy_apply,
        )
    except (LookupError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
