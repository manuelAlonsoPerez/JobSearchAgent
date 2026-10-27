"""Manual smoke runner for the artifact-requirements stage.

Two modes:

* **Persisted (default):** load the previous stage's ``JobState`` from SQLite and
  run the deterministic :func:`decide_requirements` policy.
* **Browser (``--browser``):** open the live LinkedIn Easy Apply modal, walk its
  steps without submitting, and report which artifacts the form actually asks
  for. A job whose Easy Apply flow only uploads a resume therefore needs just a
  tailored CV.

The browser mechanics live in :mod:`jobsearchagent.triage.easy_apply` and are
shared with :mod:`jobsearchagent.triage.requirements`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..browser import session
from ..database import get_job
from ..models import JobState
from ..utils.text import canonicalize_url
from .easy_apply import inspect_modal
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
        "mode": "persisted",
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


def inspect_live_requirements(
    identifier: str,
    *,
    database_path: str | Path | None = None,
    output_fn: Any = print,
) -> dict[str, Any]:
    """Open the live Easy Apply modal and report the artifacts it requires."""

    from ..discovery.linkedin import LinkedInDiscovery

    url = _resolve_job_url(identifier, database_path=database_path)
    discovery = LinkedInDiscovery(output_fn=output_fn)

    with session(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        discovery.ensure_login(page)
        discovery._navigate(page, url)
        discovery._raise_for_security_challenge(page)
        modal_result = inspect_modal(page, output_fn)

    return {
        "mode": "browser",
        "job": {"url": url},
        **modal_result,
    }


def _resolve_job_url(
    identifier: str,
    *,
    database_path: str | Path | None,
) -> str:
    """Prefer the persisted canonical URL; fall back to a direct URL argument."""

    state = get_job(identifier, path=database_path)
    if state is not None and state.get("url"):
        return str(state["url"])
    if "://" in identifier:
        return canonicalize_url(identifier)
    raise LookupError(
        f"No persisted job matches {identifier!r} and it is not a URL. Run "
        "discovery first or pass the full LinkedIn job URL."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manually test the artifact-requirements stage for one LinkedIn job."
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
        "--browser",
        action="store_true",
        help=(
            "Open the live Easy Apply modal and detect the required artifacts "
            "from the actual form instead of the deterministic policy."
        ),
    )
    parser.add_argument(
        "--force-accepted-easy-apply",
        action="store_true",
        help=(
            "Persisted mode only: smoke-test the accepted Easy Apply branch "
            "without changing the stored triage result."
        ),
    )
    arguments = parser.parse_args()

    try:
        if arguments.browser:
            result = inspect_live_requirements(
                arguments.job,
                database_path=arguments.db_path,
            )
        else:
            result = evaluate_persisted_job(
                arguments.job,
                database_path=arguments.db_path,
                force_accepted_easy_apply=arguments.force_accepted_easy_apply,
            )
    except (LookupError, ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
