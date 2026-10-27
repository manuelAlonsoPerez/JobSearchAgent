"""Persist a finished apply attempt and queue review items (A8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..database import add_to_review, upsert_job
from ..models import JobState
from ..utils.text import canonicalize_url
from .submit import LINKEDIN_SITE

_REVIEW_STATUSES = {"failed", "manual", "external"}


def record(state: JobState, *, path: str | Path | None = None) -> dict[str, Any]:
    """Write the job row and, when needed, a review_queue item with a retry link."""

    if not state.get("url"):
        raise ValueError("record() requires state['url']")
    state.setdefault("site", LINKEDIN_SITE)
    url = canonicalize_url(str(state["url"]))
    link = str(state.get("apply_url") or url)
    reason = _review_reason(state)

    upsert_job(state, path=path)

    review: dict[str, str] | None = None
    if _needs_review(state):
        add_to_review(url, reason, link, path=path)
        review = {"url": url, "reason": reason, "link": link}

    return {
        "url": url,
        "apply_status": state.get("apply_status"),
        "outcome": state.get("outcome"),
        "review_reason": state.get("review_reason"),
        "apply_url": state.get("apply_url"),
        "review": review,
    }


def _needs_review(state: JobState) -> bool:
    status = state.get("apply_status")
    if status in _REVIEW_STATUSES:
        return True
    if state.get("outcome") == "needs_confirmation":
        return True
    if status == "skipped" and state.get("review_reason"):
        return True
    return False


def _review_reason(state: JobState) -> str:
    if state.get("review_reason"):
        return str(state["review_reason"])
    status = state.get("apply_status")
    if status == "external":
        return "External application requires manual review."
    if state.get("outcome") == "needs_confirmation":
        return "Candidate fact requires confirmation."
    if status == "failed":
        return "Easy Apply failed."
    if status == "manual":
        return "Easy Apply needs review."
    return str(status or "needs review")
