"""Shared state and value models for the v1 LangGraph pipeline."""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


Outcome = Literal["accept", "reject", "needs_confirmation"]
ArtifactType = Literal["cv", "cover_letter"]
ArtifactStatus = Literal["pending", "validated", "failed"]
FormFieldType = Literal[
    "text",
    "textarea",
    "select",
    "radio",
    "checkbox",
    "file",
    "email",
    "number",
]
AnswerSource = Literal["profile", "jev", "llm", "deferred"]
ApplyStatus = Literal[
    "applied",
    "failed",
    "manual",
    "external",
    "skipped",
    "dry_run_ready",
]


class StrictModel(BaseModel):
    """Reject unknown fields so persisted state cannot drift silently."""

    model_config = ConfigDict(extra="forbid")


class FormField(StrictModel):
    id: str
    label: str
    type: FormFieldType
    options: list[str] = Field(default_factory=list)
    required: bool = False


class FieldAnswer(StrictModel):
    field_id: str
    value: str | bool | list[str] | None = None
    source: AnswerSource
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class Form(StrictModel):
    url: str
    step_label: str | None = None
    fields: list[FormField] = Field(default_factory=list)
    answers: list[FieldAnswer] = Field(default_factory=list)


class Artifact(StrictModel):
    type: ArtifactType
    path: str | None = None
    content: str | None = None
    status: ArtifactStatus = "pending"
    attempts: int = Field(default=0, ge=0)
    fact_check_passed: bool = False
    jev_score: float | None = None
    error: str | None = None


def merge_artifacts(
    left: dict[str, Artifact],
    right: dict[str, Artifact],
) -> dict[str, Artifact]:
    """LangGraph reducer: merge updates, with the newest artifact winning."""

    return {**left, **right}


class JobState(TypedDict, total=False):
    url: str
    source_job_id: str
    title: str
    company: str
    location: str
    site: str
    raw_description: str
    easy_apply: bool
    profile_id: str
    outcome: Outcome
    fit_score: float
    required_artifacts: list[ArtifactType]
    artifacts: Annotated[dict[str, Artifact], merge_artifacts]
    form: Form | None
    messages: Annotated[list[Any], add]
    apply_status: ApplyStatus
    apply_url: str | None
    review_reason: str | None
    attempts: int
