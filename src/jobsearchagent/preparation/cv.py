"""Attach the selected profile's existing source CV (A4 pass-through).

Tailored CV generation is still A4 GEN. This writer only resolves the PDF
already listed on the profile so the graph can run end-to-end.
"""

from __future__ import annotations

from ..apply.answers import resolve_cv_path
from ..models import Artifact, JobState
from ..utils.profile import load_profile


def write_cv(state: JobState) -> Artifact:
    """Return a pending CV artifact pointing at the profile source PDF."""

    profile_id = str(state.get("profile_id") or "")
    if not profile_id:
        return Artifact(type="cv", status="pending", error="No profile_id on state")
    try:
        profile = load_profile(profile_id)["profile"]
    except (KeyError, ValueError) as exc:
        return Artifact(type="cv", status="pending", error=str(exc))
    path = resolve_cv_path(state, profile)
    if path is None:
        return Artifact(
            type="cv",
            status="pending",
            error=f"No source CV in src/docs for profile {profile_id!r}",
        )
    return Artifact(type="cv", path=str(path), status="pending")
