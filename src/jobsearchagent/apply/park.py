"""Close Easy Apply without Submit and save the job for manual apply."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import JobState
from ..triage.easy_apply import dismiss_modal, save_job


def park_for_manual_apply(
    page: Any,
    state: JobState,
    *,
    reached_review: bool,
    output_fn: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Close Easy Apply without Submit and click LinkedIn Save.

    Sets ``apply_status=manual``. Callers persist with :func:`record` so the
    job lands in ``review_queue`` once. An existing ``review_reason``
    (deferred fields, etc.) is kept.
    """

    dismiss_modal(page)
    output_fn("Closed the Easy Apply modal without submitting.")
    saved = save_job(page, output_fn)
    if not state.get("review_reason"):
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
        state["review_reason"] = reason
    elif not saved:
        state["review_reason"] = (
            str(state["review_reason"]) + " Save button was not found."
        )
    state["apply_status"] = "manual"
    state["apply_url"] = state.get("apply_url") or state.get("url")
    output_fn("Queued for manual apply in review_queue.")
    return {"saved": saved}
