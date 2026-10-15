"""Text normalization, URL canonicalization, and deduplication helpers."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, quote_plus, urlsplit, urlunsplit


_WHITESPACE = re.compile(r"\s+")
_LINKEDIN_JOB_PATH = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d+)(?:[/?#]|$)")


def encode_query(value: str) -> str:
    """Encode a query or location for a URL query string."""

    return quote_plus(value.strip())


def clean_job_text(value: str) -> str:
    """Collapse repeated whitespace while preserving paragraph boundaries."""

    paragraphs = [
        _WHITESPACE.sub(" ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n", value.replace("\r\n", "\n"))
    ]
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def extract_linkedin_job_id(url: str) -> str | None:
    """Extract LinkedIn's stable numeric job ID from common URL variants."""

    match = _LINKEDIN_JOB_PATH.search(url)
    if match:
        return match.group(1)

    query = parse_qs(urlsplit(url).query)
    for key in ("currentJobId", "jobId"):
        values = query.get(key)
        if values and values[0].isdigit():
            return values[0]
    return None


def canonicalize_url(url: str) -> str:
    """Remove fragments/tracking, using a canonical LinkedIn URL when possible."""

    linkedin_job_id = extract_linkedin_job_id(url)
    if linkedin_job_id:
        return f"https://www.linkedin.com/jobs/view/{linkedin_job_id}/"

    parts = urlsplit(url.strip())
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or "/",
            "",
            "",
        )
    )


def job_dedup_key(
    site: str,
    url: str,
    source_job_id: str | None = None,
) -> str:
    """Build a stable dedup key, preferring a source-provided job ID."""

    normalized_site = _WHITESPACE.sub(" ", site).strip().casefold()
    stable_id = source_job_id or extract_linkedin_job_id(url)
    identity = stable_id.strip() if stable_id else canonicalize_url(url)
    digest = hashlib.sha256(f"{normalized_site}:{identity}".encode()).hexdigest()
    return digest
