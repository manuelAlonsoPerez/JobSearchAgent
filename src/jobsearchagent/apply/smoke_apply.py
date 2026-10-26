"""Manual smoke runner for Phase 3 Easy Apply filling.

Given a LinkedIn job URL (or a persisted job id), open the live Easy Apply
modal, fill profile-backed fields, upload the existing source CV when a resume
field is present, and stop on the Review screen. It never clicks Submit.

Cover letters and tailored CVs are skipped in this pass.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..browser import session
from ..database import get_job
from ..models import JobState
from ..triage.easy_apply import dismiss_modal, reached_submit_step
from ..triage.triage import select_profile
from ..utils.text import canonicalize_url
from .form import build_and_answer_form


def fill_live_application(
    identifier: str,
    *,
    database_path: str | Path | None = None,
    profile_id: str | None = None,
    cv_path: str | Path | None = None,
    keep_open: bool = True,
    output_fn: Any = print,
    input_fn: Any = input,
) -> dict[str, Any]:
    """Open one job, fill Easy Apply, and stop before Submit."""

    from ..config import load_work_profiles
    from ..discovery.linkedin import LinkedInDiscovery

    url, state = _resolve_job(identifier, database_path=database_path)
    if profile_id:
        state["profile_id"] = profile_id
    elif not state.get("profile_id"):
        profiles = load_work_profiles().get("work_profiles", {})
        state["profile_id"] = select_profile(state, profiles)

    discovery = LinkedInDiscovery(input_fn=input_fn, output_fn=output_fn)
    with session(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        discovery.ensure_login(page)
        discovery._navigate(page, url)
        discovery._raise_for_security_challenge(page)

        form = build_and_answer_form(
            page,
            state,
            cv_path=cv_path,
            output_fn=output_fn,
        )
        at_review = reached_submit_step(page)
        if keep_open:
            output_fn(
                "Stopped before Submit. Inspect the Review screen, then press "
                "Enter to discard the draft and close the browser."
            )
            input_fn("")
        dismiss_modal(page)

    return {
        "mode": "browser",
        "job": {
            "url": url,
            "title": state.get("title"),
            "company": state.get("company"),
            "profile_id": state.get("profile_id"),
        },
        "reached_review": at_review,
        "apply_status": state.get("apply_status"),
        "review_reason": state.get("review_reason"),
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


def _resolve_job(
    identifier: str,
    *,
    database_path: str | Path | None,
) -> tuple[str, JobState]:
    stored = get_job(identifier, path=database_path)
    if stored is not None and stored.get("url"):
        return str(stored["url"]), JobState(**stored)
    if "://" in identifier:
        url = canonicalize_url(identifier)
        return url, JobState(url=url, title="", raw_description="")
    raise LookupError(
        f"No persisted job matches {identifier!r} and it is not a URL. "
        "Pass the full LinkedIn job URL."
    )


def _display_value(value: object) -> object:
    if isinstance(value, str) and len(value) > 80:
        return value[:77] + "..."
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fill a LinkedIn Easy Apply modal for one job and stop before "
            "Submit. Does not send the application."
        )
    )
    parser.add_argument(
        "job",
        help="LinkedIn job URL or persisted job id.",
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
        help="Work-profile id to fill from (default: infer from the job title).",
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
        help="Close the browser immediately after stopping at Review.",
    )
    arguments = parser.parse_args()

    try:
        result = fill_live_application(
            arguments.job,
            database_path=arguments.db_path,
            profile_id=arguments.profile_id,
            cv_path=arguments.cv_path,
            keep_open=not arguments.no_keep_open,
        )
    except (LookupError, ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
