"""Cover-letter writer used by the apply graph.

A4 GEN (tailored letter) is not implemented. This returns an empty pending
artifact so Easy Apply can attach a letter only when the live form asks and
``state.artifacts['cover_letter']`` later gains content or a path.
"""

from __future__ import annotations

from ..models import Artifact, JobState


def write_cover_letter(state: JobState) -> Artifact:
    """Return the existing cover-letter artifact, or an empty pending one."""

    existing = (state.get("artifacts") or {}).get("cover_letter")
    if isinstance(existing, Artifact) and (existing.path or existing.content):
        return existing
    if isinstance(existing, dict) and (existing.get("path") or existing.get("content")):
        return Artifact.model_validate(existing)
    return Artifact(type="cover_letter", status="pending")
