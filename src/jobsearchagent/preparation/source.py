"""T1: resolve a work profile to its source CV and extract text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..apply.answers import resolve_cv_path
from ..utils.pdf import extract_pdf_text
from ..utils.profile import load_profile

TemplateId = Literal["servicenow", "fullstack", "ai"]


@dataclass(frozen=True)
class SourceCv:
    profile_id: str
    template_id: TemplateId
    path: Path
    raw_text: str
    context: dict[str, Any]


def resolve_source_cv(profile_id: str) -> SourceCv:
    """Load ``profile_id``, find its PDF in ``src/docs``, and extract text."""

    context = load_profile(profile_id)
    profile = context["profile"]
    path = resolve_cv_path({}, profile)
    if path is None:
        names = ", ".join(str(name) for name in profile.get("source_cvs") or [])
        raise FileNotFoundError(
            f"No source CV in src/docs for profile {profile_id!r}"
            + (f" (looked for: {names})" if names else "")
        )
    raw_text = extract_pdf_text(path)
    if not raw_text.strip():
        raise ValueError(f"Could not extract text from {path}")
    return SourceCv(
        profile_id=profile_id,
        template_id=template_id_from_filename(path.name),
        path=path,
        raw_text=raw_text,
        context=context,
    )


def template_id_from_filename(name: str) -> TemplateId:
    """Pick the HTML template from the source filename, not the LLM."""

    stem = Path(name).stem.casefold()
    if "servicenow" in stem:
        return "servicenow"
    tokens = set(stem.replace("-", " ").replace("_", " ").split())
    if "ai" in tokens:
        return "ai"
    return "fullstack"
