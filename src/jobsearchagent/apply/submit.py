"""Finish a prepared Easy Apply modal (A7).

This module resumes the browser state left by ``build_and_answer_form``. It
never restarts the application. The default path closes the modal without
Submit, clicks LinkedIn Save, and leaves the job for ``review_queue``.
``dry_run=False`` (``--live``) still clicks Submit application once.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_settings
from ..database import count_applied_today
from ..models import JobState
from ..triage.easy_apply import (
    dismiss_modal,
    easy_apply_modal_open,
    modal_root,
    reached_submit_step,
)
from .park import park_for_manual_apply

if TYPE_CHECKING:
    from playwright.sync_api import Page


_SUBMIT_APPLICATION = re.compile(
    r"submit application|enviar solicitud|send søknad|send inn",
    re.IGNORECASE,
)
_CONFIRMATION = re.compile(
    r"application (was )?sent"
    r"|application submitted"
    r"|successfully submitted"
    r"|your application was sent",
    re.IGNORECASE,
)

LINKEDIN_SITE = "LinkedIn Jobs"


def submit_application(
    page: Page | None,
    state: JobState,
    *,
    dry_run: bool | None = None,
    database_path: str | Path | None = None,
    output_fn: Callable[[str], None] = print,
) -> JobState:
    """Finish the prepared modal: save for manual apply, or one live submit."""

    settings = get_settings()
    is_dry_run = settings.dry_run if dry_run is None else dry_run
    site = str(state.get("site") or LINKEDIN_SITE)
    state.setdefault("site", site)
    _append_message(state, f"submit: dry_run={is_dry_run}")

    if state.get("easy_apply") is False:
        state["apply_status"] = "external"
        state["apply_url"] = state.get("apply_url") or state.get("url")
        state["review_reason"] = (
            state.get("review_reason")
            or "External application requires manual review."
        )
        output_fn("External application; not automating Easy Apply.")
        return state

    live_submit = not is_dry_run
    deferred = bool(state.get("apply_status") == "manual" and state.get("review_reason"))

    if live_submit:
        applied = count_applied_today(site, path=database_path)
        limit = settings.linkedin_daily_apply_limit
        if applied >= limit:
            state["apply_status"] = "skipped"
            state["review_reason"] = (
                f"Daily Easy Apply limit reached ({applied}/{limit})."
            )
            output_fn(state["review_reason"])
            if page is not None:
                dismiss_modal(page)
            return state

    if page is None:
        state["apply_status"] = "failed"
        state["review_reason"] = "Easy Apply submit was called without a browser page."
        return state

    try:
        at_review = reached_submit_step(page)
        if not live_submit or deferred or not at_review:
            if deferred:
                output_fn(
                    "Required fields were deferred; not submitting. "
                    + str(state.get("review_reason"))
                )
            elif not live_submit:
                output_fn(
                    "Last Easy Apply step reached; not clicking Submit application."
                )
            park_for_manual_apply(
                page,
                state,
                reached_review=at_review,
                output_fn=output_fn,
            )
            return state

        state["apply_url"] = state.get("apply_url") or state.get("url")

        if not _click_submit_once(page, output_fn):
            state["apply_status"] = "failed"
            state["review_reason"] = (
                "Submit application was not available on the Review screen."
            )
            _capture_diagnostics(page, state, output_fn)
            dismiss_modal(page)
            return state

        if application_confirmed(page):
            state["apply_status"] = "applied"
            state["apply_url"] = state.get("url")
            output_fn("LinkedIn confirmed the application.")
            return state

        state["apply_status"] = "failed"
        state["review_reason"] = (
            "Submit was clicked but LinkedIn did not show an application "
            "confirmation."
        )
        output_fn(state["review_reason"])
        _capture_diagnostics(page, state, output_fn)
        return state
    except Exception as exc:
        status, reason = classify_browser_error(exc)
        state["apply_status"] = status
        state["review_reason"] = reason
        output_fn(reason)
        if page is not None:
            try:
                dismiss_modal(page)
            except Exception:
                pass
        return state


def application_confirmed(page: Page) -> bool:
    """Whether LinkedIn showed a post-submit confirmation."""

    try:
        page.wait_for_timeout(800)
        text = page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return False
    return bool(_CONFIRMATION.search(text or ""))


def classify_browser_error(exc: BaseException) -> tuple[str, str]:
    """Map a browser failure to ``manual``/``failed`` plus a review reason."""

    text = str(exc)
    folded = text.casefold()
    if any(
        token in folded
        for token in (
            "security challenge",
            "checkpoint",
            "captcha",
            "login was not completed",
            "expired",
        )
    ):
        return "manual", text
    if "no visible easy apply" in folded or "easy apply modal did not open" in folded:
        return "failed", text
    return "failed", text


def _click_submit_once(page: Page, output_fn: Callable[[str], None]) -> bool:
    root = modal_root(page) if easy_apply_modal_open(page) else page.locator("body")
    button = root.get_by_role("button", name=_SUBMIT_APPLICATION).filter(visible=True)
    if not button.count():
        output_fn("No Submit application button in the Easy Apply modal.")
        return False
    control = button.first
    if not control.is_enabled():
        output_fn("Submit application is visible but disabled.")
        return False
    control.scroll_into_view_if_needed()
    control.click()
    output_fn("Clicked Submit application once.")
    return True


def _capture_diagnostics(
    page: Page,
    state: JobState,
    output_fn: Callable[[str], None],
) -> None:
    heading = ""
    try:
        if easy_apply_modal_open(page):
            heading = (modal_root(page).inner_text(timeout=3_000) or "")[:240]
    except Exception:
        heading = ""
    _append_message(
        state,
        "submit diagnostics: "
        + (heading.replace("\n", " ") or f"url={page.url}"),
    )
    output_fn("Captured Review-screen diagnostics.")


def _append_message(state: JobState, content: str) -> None:
    history = list(state.get("messages") or [])
    history.append({"role": "system", "content": content})
    state["messages"] = history
