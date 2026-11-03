"""T3: rewrite structured CV fields toward a LinkedIn offer."""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import httpx
import yaml

from ..config import get_settings
from ..models import JobState
from .fields import CvFields, initial_fields
from .source import SourceCv
from .tailor_validate import extra_grounded_skills, sanitize_cv_fields, validate_cv_fields

EXTRA_SKILL_LIMIT = 7

ChatFn = Callable[..., str]
OutputFn = Callable[[str], None]

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_JOB_DESCRIPTION_LIMIT = 2_500


def rewrite_cv(
    source: SourceCv,
    job: Mapping[str, Any] | JobState,
    *,
    max_attempts: int = 3,
    chat_fn: ChatFn | None = None,
    output_fn: OutputFn | None = None,
) -> CvFields:
    """Ask the writer model to tailor the CV; retry until fact-check passes."""

    from ..llm import chat as default_chat

    generate = chat_fn or default_chat
    baseline = initial_fields(source)
    last_errors: list[str] = ["No rewrite produced"]
    previous = ""
    timeout = get_settings().writer_timeout_seconds
    model = get_settings().writer_model

    for attempt in range(1, max_attempts + 1):
        if output_fn is not None:
            output_fn(
                f"Rewriting CV with {model} "
                f"(attempt {attempt}/{max_attempts}, timeout {timeout:g}s)"
            )
        messages = _messages(source, job, baseline, previous, last_errors, attempt)
        try:
            with _heartbeat(output_fn, "Ollama writer"):
                raw = generate(messages, model="writer", format="json")
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Ollama writer timed out after {timeout:g}s on {model}. "
                "The model may still be loading, or this machine needs a longer "
                "wait — set WRITER_TIMEOUT_SECONDS in .env and retry."
            ) from exc
        try:
            fields = _parse_fields(raw, baseline)
        except (json.JSONDecodeError, ValueError) as exc:
            last_errors = [f"Invalid JSON from writer: {exc}"]
            previous = raw[:1_500]
            if output_fn is not None:
                output_fn(f"Writer returned invalid JSON ({exc}). Retrying.")
            continue
        errors = validate_cv_fields(fields, source=source, baseline=baseline)
        if not errors:
            return finish_tailored_cv(fields, source=source, baseline=baseline, job=job)
        sanitized = sanitize_cv_fields(fields, source=source, baseline=baseline)
        leftover = validate_cv_fields(sanitized, source=source, baseline=baseline)
        if not leftover:
            if output_fn is not None:
                output_fn(
                    "Dropped ungrounded lines from the rewrite: "
                    + "; ".join(errors[:6])
                )
            return finish_tailored_cv(
                sanitized, source=source, baseline=baseline, job=job
            )
        last_errors = leftover
        previous = sanitized.model_dump_json()
        if output_fn is not None:
            output_fn("Writer JSON failed fact-check:")
            for error in leftover[:8]:
                output_fn(f"  - {error}")
            output_fn("Retrying.")

    raise RuntimeError(
        "Could not produce a fact-checked tailored CV after "
        f"{max_attempts} attempts: " + "; ".join(last_errors)
    )


def _messages(
    source: SourceCv,
    job: Mapping[str, Any],
    baseline: CvFields,
    previous: str,
    last_errors: list[str],
    attempt: int,
) -> list[dict[str, str]]:
    description = str(job.get("raw_description") or "")
    if len(description) > _JOB_DESCRIPTION_LIMIT:
        description = description[:_JOB_DESCRIPTION_LIMIT] + "\n[truncated]"
    user = f"""Tailor this CV to the job. Return JSON only with keys:
name, headline, summary, skills, languages, experience, education, contact.
experience items: organization, title, period, bullets.
education items: years, school, details (details must be a list of strings).
contact: phone, email, street, city, linkedin_label, linkedin_url.

Job title: {job.get("title") or ""}
Company: {job.get("company") or ""}
Description:
{description}

Current CV fields (JSON):
{baseline.model_dump_json()}

Allowed facts (do not invent outside this):
{_compact_facts(source)}

You may:
- set headline to the offered job title
- rewrite the summary toward the offer, keeping the same number of paragraphs and about the same word count
- rephrase bullets to emphasise the offer, keeping the same number of bullets per job and the same sentence length and style
- keep every job in the current chronological order (do not reorder)
- add at most {EXTRA_SKILL_LIMIT} extra skill lines that already appear in the allowed facts and match the offer

You may not:
- shorten the profile summary or collapse two paragraphs into one
- drop job bullets or replace a long bullet with a short fragment
- change the order of jobs
- add employers, degrees, tools, certifications, or numbers that are not in the current CV or allowed facts
- stretch years or rename job titles
- invent skills to cover gaps
"""
    if attempt > 1:
        user += (
            "\nThe previous JSON failed:\n- "
            + "\n- ".join(last_errors[:8])
        )
        if previous:
            user += "\nPreview of previous reply:\n" + previous[:1_000]
    return [
        {
            "role": "system",
            "content": (
                "You rewrite CVs. Use only facts from the current CV JSON and "
                "allowed facts. Keep the profile and each job's bullets as long as "
                "the current CV. Keep jobs in chronological order. "
                "Set headline to the offered job title. Do not invent facts."
            ),
        },
        {"role": "user", "content": user},
    ]


def _compact_facts(source: SourceCv) -> str:
    """Profile facts without match_rules or the raw PDF dump."""

    profile = source.context.get("profile") or {}
    shared = source.context.get("shared") or {}
    skills: list[str] = []
    for skill in profile.get("skills") or []:
        if isinstance(skill, dict) and skill.get("name"):
            skills.append(str(skill["name"]))
        elif skill:
            skills.append(str(skill))
    compact = {
        "summary": profile.get("summary"),
        "title": profile.get("title"),
        "email": profile.get("email"),
        "skills": skills,
        "not_claimed": profile.get("not_claimed") or [],
        "certifications": profile.get("certifications") or [],
        "experience": [
            {
                "period": job.get("period"),
                "title": job.get("title"),
                "organization": job.get("organization"),
            }
            for job in profile.get("experience") or []
            if isinstance(job, dict)
        ],
        "education": shared.get("education") if isinstance(shared, dict) else [],
        "candidate": shared.get("candidate") if isinstance(shared, dict) else {},
    }
    return yaml.safe_dump(
        compact,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()


def finish_tailored_cv(
    fields: CvFields,
    *,
    source: SourceCv,
    baseline: CvFields,
    job: Mapping[str, Any],
) -> CvFields:
    """Apply offer headline, extra skills, and source job order/length."""

    finished = restore_source_length(fields, baseline)
    finished.headline = headline_from_offer(job, baseline.headline)
    finished.skills = _with_extra_skills(finished, source, baseline, job)
    return finished


def headline_from_offer(job: Mapping[str, Any], fallback: str) -> str:
    title = re.sub(r"\s+", " ", str(job.get("title") or "")).strip()
    return title[:80] or fallback


def restore_source_length(fields: CvFields, baseline: CvFields) -> CvFields:
    """Put back summary length and chronological job order from the source CV."""

    restored = fields.model_copy(deep=True)
    restored.summary = _restore_summary(fields.summary, baseline.summary)
    rewritten = {_fold_key(job.organization): job for job in fields.experience}
    jobs = []
    for original in baseline.experience:
        key = _fold_key(original.organization)
        match = rewritten.get(key)
        if match is None:
            jobs.append(original.model_copy(deep=True))
            continue
        job = match.model_copy(deep=True)
        job.organization = original.organization
        job.title = original.title
        job.period = original.period
        job.bullets = _restore_bullets(match.bullets, original.bullets)
        jobs.append(job)
    restored.experience = jobs
    return restored


def _with_extra_skills(
    fields: CvFields,
    source: SourceCv,
    baseline: CvFields,
    job: Mapping[str, Any],
) -> list[str]:
    kept = list(baseline.skills)
    extras = extra_grounded_skills(
        _extra_skill_candidates(fields, source, job, kept),
        source=source,
        baseline=baseline,
        existing=kept,
        limit=EXTRA_SKILL_LIMIT,
    )
    return kept + extras


def _extra_skill_candidates(
    fields: CvFields,
    source: SourceCv,
    job: Mapping[str, Any],
    existing: list[str],
) -> list[str]:
    blob = f"{job.get('title') or ''} {job.get('raw_description') or ''}".casefold()
    profile = source.context.get("profile") or {}
    existing_keys = {_fold_key(item) for item in existing}
    names: list[str] = []
    for skill in fields.skills:
        if _fold_key(skill) not in existing_keys:
            names.append(skill)
    for skill in profile.get("skills") or []:
        if isinstance(skill, dict):
            label = str(skill.get("name") or "").strip()
            aliases = [str(alias) for alias in skill.get("aliases") or []]
        else:
            label = str(skill).strip()
            aliases = []
        tokens = [token for token in (label, *aliases) if token]
        if label and any(token.casefold() in blob for token in tokens):
            names.append(label)
    for item in profile.get("certifications") or []:
        text = str(item).strip()
        if text and _certification_mentioned(text, blob):
            names.append(text)
    return names


def _certification_mentioned(text: str, blob: str) -> bool:
    if text.casefold() in blob:
        return True
    return any(
        abbrev.casefold() in blob
        for abbrev in re.findall(r"\(([A-Za-z]{2,})\)", text)
    )


def _restore_summary(rewritten: str, original: str) -> str:
    if _word_count(rewritten) >= int(0.85 * max(_word_count(original), 1)):
        original_paras = _paragraphs(original)
        rewritten_paras = _paragraphs(rewritten)
        if len(rewritten_paras) >= len(original_paras):
            return rewritten
        if rewritten_paras:
            return rewritten_paras[0] + "\n\n".join(
                ["", *original_paras[len(rewritten_paras) :]]
            )
        return original
    original_paras = _paragraphs(original)
    rewritten_paras = _paragraphs(rewritten)
    if rewritten_paras and original_paras and _word_count(rewritten_paras[0]) >= 20:
        rest = original_paras[1:]
        if rest:
            return rewritten_paras[0] + "\n\n" + "\n\n".join(rest)
    return original


def _restore_bullets(rewritten: list[str], original: list[str]) -> list[str]:
    if not original:
        return rewritten
    if len(rewritten) >= len(original):
        return rewritten
    used = {_fold_key(bullet) for bullet in rewritten}
    extras = [bullet for bullet in original if _fold_key(bullet) not in used]
    return rewritten + extras[: len(original) - len(rewritten)]


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _fold_key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _parse_fields(raw: str, baseline: CvFields) -> CvFields:
    text = _FENCE.sub("", raw.strip()).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Writer JSON root must be an object")
    data = _normalize_payload(data)
    fields = CvFields.model_validate(data)
    fields.name = baseline.name or fields.name
    fields.contact = baseline.contact.model_copy()
    fields.education = [item.model_copy() for item in baseline.education]
    if not fields.languages:
        fields.languages = list(baseline.languages)
    return fields


def _normalize_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Map common writer aliases before Pydantic validation."""

    for job in data.get("experience") or []:
        if not isinstance(job, dict):
            continue
        if not job.get("organization") and job.get("company"):
            job["organization"] = job["company"]
        if job.get("bullets") is None and job.get("highlights") is not None:
            job["bullets"] = job["highlights"]
    for item in data.get("education") or []:
        if not isinstance(item, dict):
            continue
        if item.get("details") is None and item.get("degree") is not None:
            item["details"] = item["degree"]
    return data


@contextlib.contextmanager
def _heartbeat(
    output_fn: OutputFn | None,
    label: str,
    interval: float = 20.0,
) -> Iterator[None]:
    if output_fn is None:
        yield
        return
    stop = threading.Event()
    start = time.monotonic()

    def tick() -> None:
        while not stop.wait(interval):
            elapsed = int(time.monotonic() - start)
            output_fn(f"  …{label} still working ({elapsed}s elapsed)")

    thread = threading.Thread(target=tick, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)
