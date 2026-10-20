"""Determine which application artifacts a triaged job requires."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..jev import Jev
from ..models import ArtifactType, JobState


V1_REQUIRED_ARTIFACTS: tuple[ArtifactType, ...] = ("cv", "cover_letter")

# Add future artifact IDs and their binary decision instructions here. They are
# intentionally empty in v1, where CV and cover letter are always prepared.
OPTIONAL_ARTIFACT_QUESTIONS: dict[str, str] = {}


def decide_requirements(
    state: JobState,
    *,
    jev: Jev | None = None,
    optional_artifacts: Mapping[str, str] | None = None,
    noul_threshold: float = 0.5,
) -> dict[str, Any]:
    """Return required artifacts for an accepted LinkedIn Easy Apply job.

    Optional artifacts are evaluated independently with JEV ``noul`` decisions.
    The default v1 configuration contains no optional artifacts, so this path
    performs no model call.
    """

    if not 0.0 <= noul_threshold <= 1.0:
        raise ValueError("noul_threshold must be between 0 and 1")

    if state.get("outcome") != "accept" or not state.get("easy_apply"):
        return {"required_artifacts": []}

    required: list[str] = list(V1_REQUIRED_ARTIFACTS)
    questions = (
        dict(optional_artifacts)
        if optional_artifacts is not None
        else OPTIONAL_ARTIFACT_QUESTIONS
    )
    if not questions:
        return {"required_artifacts": required}

    client = jev or Jev()
    owns_client = jev is None
    decision_state = {
        "job": {
            "title": state.get("title", ""),
            "description": state.get("raw_description", "")[:10_000],
            "easy_apply": bool(state.get("easy_apply")),
        },
        "selected_profile_id": state.get("profile_id"),
    }
    try:
        for artifact_id, instructions in questions.items():
            if not artifact_id.strip() or artifact_id in required:
                continue
            probability = client.noul(decision_state, instructions)
            if probability >= noul_threshold:
                required.append(artifact_id)
    finally:
        if owns_client:
            client.close()

    return {"required_artifacts": required}
