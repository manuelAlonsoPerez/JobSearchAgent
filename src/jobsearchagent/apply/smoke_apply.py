"""Manual smoke runner for Phase 3 Easy Apply filling.

Accepts a LinkedIn job URL, a persisted job id, or the JSON object printed by
the JEV LinkedIn agent (must include ``url``). Opens the live Easy Apply
modal, fills profile-backed fields, uploads the existing source CV when a
resume field is present, and **never clicks Submit**. At the last step it
closes the modal (discarding the draft), clicks LinkedIn **Save**, and writes
the job to ``review_queue`` for later manual apply from a frontend.

Cover letters and tailored CVs are skipped in this pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..browser import session
from ..database import get_job
from ..models import JobState
from ..triage.easy_apply import dismiss_modal, reached_submit_step, save_job
from ..triage.triage import select_profile
from ..utils.profile import load_profile
from ..utils.text import canonicalize_url, extract_linkedin_job_id
from .answers import resolve_cv_path
from .form import build_and_answer_form
from .record import record

DEFAULT_DECISION_PATH = Path(__file__).resolve().with_name("decision.json")


_STATE_KEYS = (
    "url",
    "source_job_id",
    "title",
    "company",
    "location",
    "raw_description",
    "easy_apply",
    "profile_id",
    "outcome",
    "fit_score",
    "required_artifacts",
    "apply_status",
    "review_reason",
    "apply_url",
)


def fill_live_application(
    identifier: str | Mapping[str, Any] | None = None,
    *,
    database_path: str | Path | None = None,
    profile_id: str | None = None,
    cv_path: str | Path | None = None,
    keep_open: bool = True,
    output_fn: Any = print,
    input_fn: Any = input,
) -> dict[str, Any]:
    """Open one job, fill Easy Apply, close without Submit, save, and queue."""

    from ..config import load_work_profiles
    from ..discovery.linkedin import LinkedInDiscovery

    url, state = resolve_job(identifier, database_path=database_path)
    if profile_id:
        state["profile_id"] = profile_id
    elif not state.get("profile_id"):
        profiles = load_work_profiles().get("work_profiles", {})
        state["profile_id"] = select_profile(state, profiles)

    selected_profile = str(state.get("profile_id") or "")
    resume = resolve_cv_path(
        state,
        load_profile(selected_profile)["profile"],
        override=cv_path,
    )
    if resume is None:
        raise FileNotFoundError(
            f"No source CV in src/docs for profile {selected_profile!r}. "
            "Add the PDF named in work_profiles.yaml source_cvs, or pass --cv."
        )
    output_fn(f"Using profile '{selected_profile}' CV: {resume}")

    discovery = LinkedInDiscovery(input_fn=input_fn, output_fn=output_fn)
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
        at_review = reached_submit_step(page)
        output_fn(
            "Last Easy Apply step reached; not clicking Submit application."
        )
        persisted = park_for_manual_apply(
            page,
            state,
            reached_review=at_review,
            database_path=database_path,
            output_fn=output_fn,
        )
        if keep_open:
            output_fn(
                "Job saved and queued for manual apply. Inspect the page, "
                "then press Enter to close the browser."
            )
            input_fn("")

    return {
        "mode": "browser",
        "job": {
            "url": url,
            "title": state.get("title"),
            "company": state.get("company"),
            "location": state.get("location"),
            "profile_id": state.get("profile_id"),
            "outcome": state.get("outcome"),
        },
        "reached_review": at_review,
        "saved": persisted.get("saved"),
        "apply_status": state.get("apply_status"),
        "review_reason": state.get("review_reason"),
        "review": persisted.get("review"),
        "step_label": form.step_label,
        "answers": [
            {
                "label": next(
                    (field.label for field in form.fields if field.id == answer.field_id),
                    answer.field_id,
                ),
                "value": _display_value(answer.value),
                "source": answer.source,
            }
            for answer in form.answers
        ],
    }


def park_for_manual_apply(
    page: Any,
    state: JobState,
    *,
    reached_review: bool,
    database_path: str | Path | None = None,
    output_fn: Any = print,
) -> dict[str, Any]:
    """Close Easy Apply without Submit, click Save, and write ``review_queue``."""

    dismiss_modal(page)
    output_fn("Closed the Easy Apply modal without submitting.")
    saved = save_job(page, output_fn)
    if reached_review:
        reason = (
            "Easy Apply filled and closed without Submit; "
            "saved for manual apply."
        )
    else:
        reason = (
            "Easy Apply did not reach Review; closed without Submit "
            "and queued for manual apply."
        )
    if not saved:
        reason += " Save button was not found."
    state["apply_status"] = "manual"
    state["review_reason"] = reason
    persisted = record(state, path=database_path)
    output_fn("Queued for manual apply in review_queue.")
    return {"saved": saved, **persisted}


def resolve_job(
    identifier: str | Mapping[str, Any] | None,
    *,
    database_path: str | Path | None = None,
) -> tuple[str, JobState]:
    """Resolve a URL / job id / JEV decision object into a ``JobState``."""

    if isinstance(identifier, Mapping):
        return _from_decision(identifier, database_path=database_path)

    raw = (identifier or "").strip()
    if raw.startswith("{"):
        return _from_decision(parse_decision(raw), database_path=database_path)

    as_file = Path(raw).expanduser() if raw and raw != "-" else None
    if as_file is not None and as_file.is_file():
        return _from_decision(
            parse_decision(as_file.read_text(encoding="utf-8")),
            database_path=database_path,
        )

    stored = get_job(raw, path=database_path) if raw else None
    if stored is not None and stored.get("url"):
        return str(stored["url"]), JobState(**stored)
    if raw and "://" in raw:
        url = canonicalize_url(raw)
        return url, _merge_stored(JobState(url=url), database_path)
    raise LookupError(
        "Pass a JEV decision JSON object (with url), a JSON file, a LinkedIn "
        "job URL, or a persisted job id."
    )


def parse_decision(raw: str) -> dict[str, Any]:
    """Parse the JSON object printed by the LinkedIn JEV agent."""

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("JEV decision JSON must be an object")
    if not payload.get("url"):
        raise ValueError("JEV decision JSON must include a 'url' field")
    return payload


def _from_decision(
    payload: Mapping[str, Any],
    *,
    database_path: str | Path | None,
) -> tuple[str, JobState]:
    url = canonicalize_url(str(payload["url"]))
    details = payload.get("details")
    profile = details.get("profile") if isinstance(details, Mapping) else None
    profile_id = payload.get("profile_id")
    if not profile_id and isinstance(profile, Mapping):
        profile_id = profile.get("choice")

    state = JobState(url=url, easy_apply=True)
    for key in _STATE_KEYS:
        value = payload.get(key)
        if value is not None and key != "url":
            state[key] = value  # type: ignore[literal-required]
    if profile_id:
        state["profile_id"] = str(profile_id)
    job_id = extract_linkedin_job_id(url)
    if job_id:
        state["source_job_id"] = job_id
    return url, _merge_stored(state, database_path)


def _merge_stored(
    state: JobState,
    database_path: str | Path | None,
) -> JobState:
    """Fill gaps (description, company) from SQLite when the job was triaged."""

    identifier = str(state.get("source_job_id") or state.get("url") or "")
    if not identifier:
        return state
    stored = get_job(identifier, path=database_path)
    if stored is None:
        return state
    merged = JobState(**stored)
    for key, value in state.items():
        if value not in (None, "", []):
            merged[key] = value  # type: ignore[literal-required]
    return merged


def _display_value(value: object) -> object:
    if isinstance(value, str) and len(value) > 80:
        return value[:77] + "..."
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fill a LinkedIn Easy Apply modal, close it without Submit, "
            "click Save, and queue the job for manual apply. Defaults to "
            "apply/decision.json in this folder."
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
        help=(
            "Path to the JEV agent's JSON output, or '-' to read that object "
            "from stdin."
        ),
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
        "--no-keep-open",
        action="store_true",
        help="Close the browser immediately after Save and queue.",
    )
    arguments = parser.parse_args()

    try:
        identifier = _load_identifier(arguments.job, arguments.from_json)
        result = fill_live_application(
            identifier,
            database_path=arguments.db_path,
            profile_id=arguments.profile_id,
            cv_path=arguments.cv_path,
            keep_open=not arguments.no_keep_open,
        )
    except (LookupError, ValueError, FileNotFoundError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2, ensure_ascii=False))


def _load_identifier(
    job: str | None,
    from_json: str | None,
) -> str | dict[str, Any]:
    if from_json == "-" or (from_json is None and job == "-"):
        return parse_decision(sys.stdin.read())
    if from_json:
        path = Path(from_json).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"JEV JSON file not found: {path}")
        return parse_decision(path.read_text(encoding="utf-8"))
    if job:
        return job
    if DEFAULT_DECISION_PATH.is_file():
        return parse_decision(DEFAULT_DECISION_PATH.read_text(encoding="utf-8"))
    if not sys.stdin.isatty():
        return parse_decision(sys.stdin.read())
    raise LookupError(
        f"No job given and {DEFAULT_DECISION_PATH} is missing. Pass "
        "--from-json, a job URL, or add decision.json next to smoke_apply.py."
    )


if __name__ == "__main__":
    main()
