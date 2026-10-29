"""Validate prepared artifacts before the Easy Apply modal (A5 pass-through).

Full A5 (JEV quality score + writer retry) is not wired yet. This gate only
checks that a required CV file exists and that cover-letter text, if present,
does not invent ``not_claimed`` skills.
"""

from __future__ import annotations

from pathlib import Path

from ..models import Artifact, JobState
from ..utils.profile import load_profile


def validate(artifact: Artifact, state: JobState) -> Artifact:
    """Mark one artifact validated or failed. Never invents missing content."""

    attempts = int(artifact.attempts) + 1
    if artifact.type == "cv":
        path = Path(artifact.path).expanduser() if artifact.path else None
        if path is not None and path.is_file():
            return artifact.model_copy(
                update={
                    "status": "validated",
                    "fact_check_passed": True,
                    "attempts": attempts,
                    "error": None,
                }
            )
        return artifact.model_copy(
            update={
                "status": "failed",
                "fact_check_passed": False,
                "attempts": attempts,
                "error": artifact.error or "CV file is missing",
            }
        )

    text = str(artifact.content or "")
    if text and _invents_not_claimed(text, state):
        return artifact.model_copy(
            update={
                "status": "failed",
                "fact_check_passed": False,
                "attempts": attempts,
                "error": "Cover letter uses a skill marked not_claimed",
            }
        )
    return artifact.model_copy(
        update={
            "status": "validated",
            "fact_check_passed": True,
            "attempts": attempts,
            "error": None,
        }
    )


def _invents_not_claimed(text: str, state: JobState) -> bool:
    profile_id = str(state.get("profile_id") or "")
    if not profile_id:
        return False
    try:
        profile = load_profile(profile_id)["profile"]
    except (KeyError, ValueError):
        return False
    folded = text.casefold()
    for item in profile.get("not_claimed") or []:
        name = str(item).strip()
        if name and name.casefold() in folded:
            return True
    return False
