"""Standalone tailored-CV agent.

Given a work-profile id and a LinkedIn job URL, resolve the matching PDF in
``src/docs``, rewrite it toward the offer, and write a new file named with the
job. With neither flag, tailor every pending ``review_queue`` row that has a
``profile_id``. Does not open Easy Apply and does not Submit.

    PYTHONPATH=src python -m jobsearchagent.tailor_cv \\
      --profile servicenow-developer \\
      --url https://www.linkedin.com/jobs/view/1234567890/

    PYTHONPATH=src python -m jobsearchagent.tailor_cv
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from .browser import session
from .config import get_settings
from .database import get_job_detail, list_review_queue
from .discovery.linkedin import LinkedInDiscovery
from .models import JobState
from .preparation.fields import CvFields
from .preparation.render import render_cv_pdf
from .preparation.rewrite import ChatFn, rewrite_cv
from .preparation.source import SourceCv, resolve_source_cv
from .utils.profile import load_work_profiles
from .utils.text import canonicalize_url, extract_linkedin_job_id

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

OutputFn = Callable[[str], None]
ScrapeFn = Callable[[str], JobState]


def _print(message: str) -> None:
    print(message, flush=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    profiles = sorted(
        str(key) for key in (load_work_profiles().get("work_profiles") or {})
    )
    parser = argparse.ArgumentParser(
        description=(
            "Create a tailored CV for one LinkedIn job, or for every pending "
            "review_queue job when --profile and --url are omitted. "
            "Standalone: does not apply and does not Submit."
        )
    )
    parser.add_argument(
        "--profile",
        metavar="ID",
        help="Work-profile id from work_profiles.yaml: " + ", ".join(profiles),
    )
    parser.add_argument(
        "--url",
        help="LinkedIn /jobs/view/… URL",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the new PDF. Default: /Users/macnolo/Desktop/Trabajo",
    )
    return parser.parse_args(argv)


def tailored_filename(job: JobState) -> str:
    """``{job-id} - {company} - {job-title}.pdf`` with unsafe characters stripped."""

    job_id = (
        str(job.get("source_job_id") or "").strip()
        or extract_linkedin_job_id(str(job.get("url") or ""))
        or "unknown"
    )
    company = _safe_part(str(job.get("company") or ""), "Unknown company")
    title = _safe_part(str(job.get("title") or ""), "Role")
    return f"{job_id} - {company} - {title}.pdf"


def tailored_output_path(job: JobState, directory: str | Path | None = None) -> Path:
    root = (
        Path(directory)
        if directory is not None
        else get_settings().tailored_cv_dir
    )
    return Path(root).expanduser().resolve() / tailored_filename(job)


def scrape_linkedin_job(
    url: str,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: OutputFn = print,
) -> JobState:
    """Scrape one job posting with the persistent Chrome profile."""

    from .config import refuse_linkedin_automation

    refuse_linkedin_automation()
    discovery = LinkedInDiscovery(input_fn=input_fn, output_fn=output_fn)
    with session(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        discovery.ensure_login(page)
        return discovery.scrape_job(page, canonicalize_url(url))


def tailor(
    profile_id: str,
    url: str,
    *,
    output_dir: str | Path | None = None,
    scrape_fn: ScrapeFn | None = None,
    chat_fn: ChatFn | None = None,
    source: SourceCv | None = None,
    job: Mapping[str, Any] | JobState | None = None,
    output_fn: OutputFn = print,
) -> Path:
    """Resolve CV, scrape job, rewrite, fact-check, and write a new PDF."""

    loaded = source or resolve_source_cv(profile_id)
    output_fn(f"Source CV: {loaded.path} ({loaded.template_id})")
    resolved = dict(job) if job is not None else (scrape_fn or scrape_linkedin_job)(url)
    if not resolved.get("url"):
        resolved["url"] = canonicalize_url(url)
    output_fn(
        f"Job: {resolved.get('title') or 'Untitled'} — {resolved.get('company') or '?'}"
    )
    fields: CvFields = rewrite_cv(
        loaded,
        resolved,
        max_attempts=get_settings().max_artifact_attempts,
        chat_fn=chat_fn,
        output_fn=output_fn,
    )
    destination = tailored_output_path(resolved, output_dir)
    render_cv_pdf(loaded.template_id, fields, destination)
    output_fn(str(destination))
    return destination


def tailor_review_queue(
    *,
    output_dir: str | Path | None = None,
    scrape_fn: ScrapeFn | None = None,
    chat_fn: ChatFn | None = None,
    output_fn: OutputFn = print,
    db_path: str | Path | None = None,
    queue_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> int:
    """Write a tailored CV for each pending review_queue row with a profile."""

    items = (
        queue_fn()
        if queue_fn is not None
        else list_review_queue(path=db_path, status="pending")
    )
    if not items:
        output_fn("No pending review_queue jobs.")
        return 1

    output_fn(f"Tailoring CVs for {len(items)} pending review_queue job(s).")
    sources: dict[str, SourceCv] = {}
    written = 0
    failed = 0
    skipped = 0
    for index, item in enumerate(items, 1):
        url = str(item.get("url") or item.get("link") or "").strip()
        profile_id = str(item.get("profile_id") or "").strip()
        label = str(item.get("title") or url or f"item {item.get('id')}")
        output_fn(f"[{index}/{len(items)}] {label}")
        if not url:
            output_fn("  skip: no URL")
            skipped += 1
            continue
        if not profile_id:
            output_fn("  skip: no profile_id")
            skipped += 1
            continue
        try:
            if profile_id not in sources:
                sources[profile_id] = resolve_source_cv(profile_id)
            stored = _job_from_review_item(item, db_path)
            if stored is None:
                output_fn("  No stored description; scraping LinkedIn.")
            tailor(
                profile_id,
                url,
                output_dir=output_dir,
                scrape_fn=scrape_fn,
                chat_fn=chat_fn,
                source=sources[profile_id],
                job=stored,
                output_fn=output_fn,
            )
            written += 1
        except (
            KeyError,
            FileNotFoundError,
            ValueError,
            RuntimeError,
            httpx.TimeoutException,
        ) as exc:
            output_fn(f"  failed: {_format_tailor_error(exc)}")
            failed += 1

    output_fn(f"Done. wrote={written} failed={failed} skipped={skipped}")
    if written == 0 or failed:
        return 1
    return 0


def _job_from_review_item(
    item: Mapping[str, Any],
    db_path: str | Path | None,
) -> JobState | None:
    """Reuse a stored jobs-table description when the review URL matches."""

    identifiers = [
        item.get("url"),
        item.get("link"),
        item.get("source_job_id"),
        item.get("dedup_key"),
    ]
    for identifier in identifiers:
        text = str(identifier or "").strip()
        if not text:
            continue
        try:
            detail = get_job_detail(text, path=db_path)
        except ValueError:
            continue
        if not detail or not str(detail.get("raw_description") or "").strip():
            continue
        return {
            "url": str(detail.get("url") or item.get("url") or ""),
            "source_job_id": str(
                detail.get("source_job_id") or item.get("source_job_id") or ""
            ),
            "title": str(detail.get("title") or item.get("title") or ""),
            "company": str(detail.get("company") or item.get("company") or ""),
            "location": str(detail.get("location") or ""),
            "site": str(detail.get("site") or "LinkedIn Jobs"),
            "raw_description": str(detail["raw_description"]),
            "easy_apply": bool(detail.get("easy_apply")),
            "profile_id": str(item.get("profile_id") or detail.get("profile_id") or ""),
            "apply_url": detail.get("apply_url") or item.get("link"),
            "employment_type": str(detail.get("employment_type") or ""),
        }
    return None


def _format_tailor_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        timeout = get_settings().writer_timeout_seconds
        return (
            f"Ollama writer timed out after {timeout:g}s. "
            "Set WRITER_TIMEOUT_SECONDS in .env and retry."
        )
    return str(exc)


def main(
    argv: Sequence[str] | None = None,
    *,
    output_fn: OutputFn = _print,
    scrape_fn: ScrapeFn | None = None,
    chat_fn: ChatFn | None = None,
    db_path: str | Path | None = None,
    queue_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> int:
    arguments = parse_args(argv)
    profile = (arguments.profile or "").strip()
    url = (arguments.url or "").strip()
    if bool(profile) ^ bool(url):
        output_fn(
            "Pass both --profile and --url, or neither to tailor every "
            "pending review_queue job."
        )
        return 1
    if not profile:
        return tailor_review_queue(
            output_dir=arguments.output_dir,
            scrape_fn=scrape_fn,
            chat_fn=chat_fn,
            output_fn=output_fn,
            db_path=db_path,
            queue_fn=queue_fn,
        )
    try:
        tailor(
            profile,
            url,
            output_dir=arguments.output_dir,
            scrape_fn=scrape_fn,
            chat_fn=chat_fn,
            output_fn=output_fn,
        )
    except (
        KeyError,
        FileNotFoundError,
        ValueError,
        RuntimeError,
        httpx.TimeoutException,
    ) as exc:
        output_fn(_format_tailor_error(exc))
        return 1
    return 0


def _safe_part(value: str, fallback: str) -> str:
    cleaned = _UNSAFE.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or fallback


if __name__ == "__main__":
    raise SystemExit(main())
