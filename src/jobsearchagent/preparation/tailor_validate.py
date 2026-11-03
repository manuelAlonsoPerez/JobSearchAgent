"""T4: deterministic fact-check for tailored CV JSON."""

from __future__ import annotations

import re
from typing import Any

import yaml

from .fields import CvFields, ExperienceRole, fields_to_grounding_text, profile_allowlist
from .source import SourceCv

_TOKEN = re.compile(r"[a-z0-9+]{4,}")
_STOP = {
    "with",
    "from",
    "that",
    "this",
    "using",
    "into",
    "and",
    "for",
    "the",
    "years",
    "year",
    "present",
}


def validate_cv_fields(
    fields: CvFields,
    *,
    source: SourceCv,
    baseline: CvFields | None = None,
) -> list[str]:
    """Return fact-check errors. Empty means the rewrite is grounded."""

    corpus, allow, skill_vocab = _grounding(source, baseline)
    profile = source.context.get("profile") or {}
    source_blob = _fold(source.raw_text)
    errors: list[str] = []
    output_blob = _fold(fields_to_grounding_text(fields))

    for item in profile.get("not_claimed") or []:
        name = str(item).strip()
        if not name:
            continue
        if _contains_phrase(output_blob, name) and not _contains_phrase(source_blob, name):
            errors.append(f"Uses not_claimed skill {name!r}")

    for job in fields.experience:
        if not _label_allowed(job.organization, allow["organizations"], corpus):
            errors.append(f"Unknown employer {job.organization!r}")
        if not _label_allowed(job.title, allow["titles"], corpus):
            errors.append(f"Unknown job title {job.title!r}")
        if not _label_allowed(job.period, allow["periods"], corpus):
            errors.append(f"Unknown period {job.period!r}")

    for skill in fields.skills:
        if not _skill_allowed(skill, corpus, allow["skills"], skill_vocab):
            errors.append(f"Unknown skill {skill!r}")

    for item in fields.education:
        if not _label_allowed(item.school, allow["organizations"], corpus):
            errors.append(f"Unknown school {item.school!r}")

    return errors


def sanitize_cv_fields(
    fields: CvFields,
    *,
    source: SourceCv,
    baseline: CvFields,
) -> CvFields:
    """Drop invented employers/skills and pin identity fields from the source CV."""

    corpus, allow, skill_vocab = _grounding(source, baseline)
    cleaned = fields.model_copy(deep=True)
    cleaned.name = baseline.name
    cleaned.contact = baseline.contact.model_copy()
    cleaned.education = [item.model_copy() for item in baseline.education]
    if not cleaned.languages:
        cleaned.languages = list(baseline.languages)

    kept_jobs: list[ExperienceRole] = []
    for job in cleaned.experience:
        if (
            _label_allowed(job.organization, allow["organizations"], corpus)
            and _label_allowed(job.title, allow["titles"], corpus)
            and _label_allowed(job.period, allow["periods"], corpus)
        ):
            kept_jobs.append(job)
    cleaned.experience = kept_jobs or [
        job.model_copy(deep=True) for job in baseline.experience
    ]

    kept_skills = [
        skill
        for skill in cleaned.skills
        if _skill_allowed(skill, corpus, allow["skills"], skill_vocab)
    ]
    cleaned.skills = kept_skills or list(baseline.skills)

    leftover = validate_cv_fields(cleaned, source=source, baseline=baseline)
    if any(error.startswith("Uses not_claimed") for error in leftover):
        cleaned.summary = baseline.summary
        cleaned.skills = list(baseline.skills)
    return cleaned


def extra_grounded_skills(
    candidates: list[str],
    *,
    source: SourceCv,
    baseline: CvFields,
    existing: list[str],
    limit: int = 7,
) -> list[str]:
    """Return up to ``limit`` extra skill lines that are already on the profile."""

    corpus, allow, skill_vocab = _grounding(source, baseline)
    seen = {_fold(item) for item in existing}
    extras: list[str] = []
    for raw in candidates:
        name = str(raw).strip()
        key = _fold(name)
        if not name or key in seen:
            continue
        if not _skill_allowed(name, corpus, allow["skills"], skill_vocab):
            continue
        extras.append(name)
        seen.add(key)
        if len(extras) >= limit:
            break
    return extras


def _grounding(
    source: SourceCv,
    baseline: CvFields | None,
) -> tuple[str, dict[str, set[str]], set[str]]:
    source_blob = _fold(source.raw_text)
    yaml_blob = _fold(
        yaml.safe_dump(source.context, allow_unicode=True, sort_keys=False)
    )
    baseline_blob = _fold(fields_to_grounding_text(baseline)) if baseline else ""
    corpus = "\n".join(part for part in (source_blob, yaml_blob, baseline_blob) if part)
    allow = profile_allowlist(source.context)
    if baseline is not None:
        for job in baseline.experience:
            allow["organizations"].add(job.organization)
            allow["titles"].add(job.title)
            allow["periods"].add(job.period)
        allow["skills"].update(baseline.skills)
        for item in baseline.education:
            allow["organizations"].add(item.school)
            allow["periods"].add(item.years)
    skill_vocab = _token_set([*allow["skills"], *(baseline.skills if baseline else [])])
    return corpus, allow, skill_vocab


def _skill_allowed(
    skill: str,
    corpus: str,
    allowed_skills: set[str],
    skill_vocab: set[str],
) -> bool:
    folded = _fold(skill)
    if folded in corpus or folded in _fold("\n".join(allowed_skills)):
        return True
    tokens = [token for token in _TOKEN.findall(folded) if token not in _STOP]
    if not tokens:
        return True
    # Only the skill vocabulary, not the whole CV body: a tool mentioned in
    # one job bullet is not a new sidebar skill.
    return all(token in skill_vocab for token in tokens)


def _label_allowed(value: str, allowed: set[str], corpus: str) -> bool:
    folded = _fold(value)
    if not folded:
        return False
    if any(_fold(item) == folded or _fold(item) in folded or folded in _fold(item) for item in allowed):
        return True
    return folded in corpus


def _token_set(values: list[Any]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(_TOKEN.findall(_fold(str(value))))
    return tokens


def _contains_phrase(blob: str, phrase: str) -> bool:
    folded = _fold(phrase)
    if not folded:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])", blob) is not None


def _fold(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()
