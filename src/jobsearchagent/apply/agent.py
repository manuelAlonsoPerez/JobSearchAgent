"""Phase 3 apply agent: fill (A6), submit (A7), and record (A8).

Uses the same job input as ``smoke_apply`` (decision.json / URL / job id).
Unlike the smoke runner it persists the result, enforces the daily apply
limit, and either discards the draft (``DRY_RUN``) or submits once.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..browser import session
from ..config import get_settings
from ..database import count_applied_today, init_db
from ..discovery.linkedin import LinkedInDiscovery
from ..models import JobState
from ..triage.triage import select_profile
from ..utils.profile import load_profile
from .answers import resolve_cv_path
from .form import build_and_answer_form
from .record import record
from .smoke_apply import _display_value, _load_identifier, resolve_job
from .submit import LINKEDIN_SITE, classify_browser_error, submit_application


def apply_to_job(
    identifier: str | Mapping[str, Any] | None = None,
    *,
    database_path: str | Path | None = None,
    profile_id: str | None = None,
    cv_path: str | Path | None = None,
    dry_run: bool | None = None,
    keep_open: bool = False,
    output_fn: Any = print,
    input_fn: Any = input,
) -> dict[str, Any]:
    """Fill one Easy Apply job, submit or dry-run, and persist the result."""

    from ..config import load_work_profiles

    settings = get_settings()
    is_dry_run = settings.dry_run if dry_run is None else dry_run
    init_db(database_path)

    url, state = resolve_job(identifier, database_path=database_path)
    state.setdefault("site", LINKEDIN_SITE)
    state.setdefault("url", url)
    if profile_id:
        state["profile_id"] = profile_id
    elif not state.get("profile_id"):
        profiles = load_work_profiles().get("work_profiles", {})
        state["profile_id"] = select_profile(state, profiles)

    if state.get("easy_apply") is False:
        submit_application(
            None,
            state,
            dry_run=is_dry_run,
            database_path=database_path,
            output_fn=output_fn,
        )
        persisted = record(state, path=database_path)
        return _result(state, persisted, form_answers=[])

    if not is_dry_run:
        applied = count_applied_today(LINKEDIN_SITE, path=database_path)
        if applied >= settings.linkedin_daily_apply_limit:
            state["apply_status"] = "skipped"
            state["review_reason"] = (
                f"Daily Easy Apply limit reached "
                f"({applied}/{settings.linkedin_daily_apply_limit})."
            )
            output_fn(state["review_reason"])
            persisted = record(state, path=database_path)
            return _result(state, persisted, form_answers=[])

    selected_profile = str(state.get("profile_id") or "")
    resume = resolve_cv_path(
        state,
        load_profile(selected_profile)["profile"],
        override=cv_path,
    )
    if resume is None:
        state["apply_status"] = "failed"
        state["review_reason"] = (
            f"No source CV in src/docs for profile {selected_profile!r}."
        )
        persisted = record(state, path=database_path)
        return _result(state, persisted, form_answers=[])
    output_fn(f"Using profile '{selected_profile}' CV: {resume}")
    output_fn("Mode: " + ("dry run" if is_dry_run else "live submit"))

    discovery = LinkedInDiscovery(input_fn=input_fn, output_fn=output_fn)
    form_answers: list[dict[str, Any]] = []
    try:
        with session(headless=False) as context:
            page = context.pages[0] if context.pages else context.new_page()
            discovery.ensure_login(page)
            discovery._navigate(page, url)
            discovery._raise_for_security_challenge(page)

            form = build_and_answer_form(
                page,
                state,
                cv_path=resume,
                output_fn=output_fn,
            )
            form_answers = [
                {
                    "label": next(
                        (
                            field.label
                            for field in form.fields
                            if field.id == answer.field_id
                        ),
                        answer.field_id,
                    ),
                    "value": _display_value(answer.value),
                    "source": answer.source,
                }
                for answer in form.answers
            ]
            submit_application(
                page,
                state,
                dry_run=is_dry_run,
                database_path=database_path,
                output_fn=output_fn,
            )
            if keep_open:
                output_fn("Inspect the browser, then press Enter to close.")
                input_fn("")
    except Exception as exc:
        status, reason = classify_browser_error(exc)
        state["apply_status"] = status
        state["review_reason"] = reason
        output_fn(reason)

    persisted = record(state, path=database_path)
    return _result(state, persisted, form_answers)


def _result(
    state: JobState,
    persisted: Mapping[str, Any],
    form_answers: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "mode": "apply",
        "job": {
            "url": state.get("url"),
            "title": state.get("title"),
            "company": state.get("company"),
            "location": state.get("location"),
            "profile_id": state.get("profile_id"),
            "outcome": state.get("outcome"),
        },
        "apply_status": state.get("apply_status"),
        "review_reason": state.get("review_reason"),
        "apply_url": state.get("apply_url"),
        "review": persisted.get("review"),
        "answers": form_answers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply to one LinkedIn Easy Apply job: fill the modal, then dry-run "
            "or submit once, and record the result. Defaults to "
            "apply/decision.json. DRY_RUN=true (default) never clicks Submit."
        )
    )
    parser.add_argument(
        "job",
        nargs="?",
        help=(
            "JEV decision JSON, path to that JSON file, LinkedIn job URL, "
            "or persisted job id. Defaults to apply/decision.json."
        ),
    )
    parser.add_argument(
        "--from-json",
        dest="from_json",
        default=None,
        help="Path to JEV JSON, or '-' to read that object from stdin.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override DB_PATH from .env.",
    )
    parser.add_argument(
        "--profile-id",
        default=None,
        help="Work-profile id to fill from (default: JEV profile_id or title).",
    )
    parser.add_argument(
        "--cv",
        dest="cv_path",
        default=None,
        help="Path to the CV PDF to upload. Defaults to the profile source CV.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Stop at Review and discard the draft (default when DRY_RUN=true).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Click Submit application once. Overrides DRY_RUN for this job.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave the browser open until Enter is pressed.",
    )
    arguments = parser.parse_args()
    if arguments.live and arguments.dry_run:
        parser.error("Use either --live or --dry-run, not both.")

    try:
        identifier = _load_identifier(arguments.job, arguments.from_json)
        is_dry_run = False if arguments.live else arguments.dry_run
        result = apply_to_job(
            identifier,
            database_path=arguments.db_path,
            profile_id=arguments.profile_id,
            cv_path=arguments.cv_path,
            dry_run=is_dry_run,
            keep_open=arguments.keep_open,
        )
    except (LookupError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
