"""Standalone tailored-CV agent.

Given a work-profile id and a LinkedIn job URL, resolve the matching PDF in
``src/docs``, rewrite it toward the offer, and write a new file named with the
job. Does not open Easy Apply and does not Submit.

    PYTHONPATH=src python -m jobsearchagent.tailor_cv \\
      --profile servicenow-developer \\
      --url https://www.linkedin.com/jobs/view/1234567890/
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

from .browser import session
from .config import get_settings
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
            "Create a tailored CV for one LinkedIn job from a chosen work profile. "
            "Standalone: does not apply and does not Submit."
        )
    )
    parser.add_argument(
        "--profile",
        required=True,
        metavar="ID",
        help="Work-profile id from work_profiles.yaml: " + ", ".join(profiles),
    )
    parser.add_argument(
        "--url",
        required=True,
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
    output_fn: OutputFn = print,
) -> Path:
    """Resolve CV, scrape job, rewrite, fact-check, and write a new PDF."""

    loaded = source or resolve_source_cv(profile_id)
    output_fn(f"Source CV: {loaded.path} ({loaded.template_id})")
    job = (scrape_fn or scrape_linkedin_job)(url)
    output_fn(
        f"Job: {job.get('title') or 'Untitled'} — {job.get('company') or '?'}"
    )
    fields: CvFields = rewrite_cv(
        loaded,
        job,
        max_attempts=get_settings().max_artifact_attempts,
        chat_fn=chat_fn,
        output_fn=output_fn,
    )
    destination = tailored_output_path(job, output_dir)
    render_cv_pdf(loaded.template_id, fields, destination)
    output_fn(str(destination))
    return destination


def main(
    argv: Sequence[str] | None = None,
    *,
    output_fn: OutputFn = _print,
    scrape_fn: ScrapeFn | None = None,
    chat_fn: ChatFn | None = None,
) -> int:
    arguments = parse_args(argv)
    try:
        tailor(
            arguments.profile,
            arguments.url,
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
        if isinstance(exc, httpx.TimeoutException):
            timeout = get_settings().writer_timeout_seconds
            output_fn(
                f"Ollama writer timed out after {timeout:g}s. "
                "Set WRITER_TIMEOUT_SECONDS in .env and retry."
            )
        else:
            output_fn(str(exc))
        return 1
    return 0


def _safe_part(value: str, fallback: str) -> str:
    cleaned = _UNSAFE.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or fallback


if __name__ == "__main__":
    raise SystemExit(main())
