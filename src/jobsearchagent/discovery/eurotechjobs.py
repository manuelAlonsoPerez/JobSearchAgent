"""EuroTechJobs discovery. One adapter serves both enabled rows.

``eurotechjobs`` fills ``{query_encoded}`` with the token the run loop already
prepared (``ServiceNow developer`` → ``servicenow``). ``eurotechjobs-remote``
opens its category URL once. Dates and, on the search row, work type come
from the card text. The detail page supplies the description and apply link.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus, urljoin

from ..config import Site
from ..database import seen, upsert_job
from ..models import JobState
from ..utils.text import canonicalize_url, clean_job_text
from .linkedin import parse_posted_at, posted_too_old, posted_window_seconds

if TYPE_CHECKING:
    from .registry import SearchFilters


_CARD = re.compile(r'<li class="standardJobContainer">(.*?)</li>', re.IGNORECASE | re.DOTALL)
_JOB_HREF = re.compile(r'href="(/job_display/(\d+)/[^"]*)"', re.IGNORECASE)
_H3 = re.compile(r"<h3[^>]*>\s*<a[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_COMPANY = re.compile(r'<div class="companyName">(.*?)</div>', re.IGNORECASE | re.DOTALL)
_LOCATION = re.compile(r'<div class="location">(.*?)</div>', re.IGNORECASE | re.DOTALL)
_POSTED = re.compile(r'<div class="postedDate">(.*?)</div>', re.IGNORECASE | re.DOTALL)
_PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_DISPLAY = re.compile(
    r'class="jobDisplay">(.*?)(?:<div class="apply\b|$)',
    re.IGNORECASE | re.DOTALL,
)
_APPLY = re.compile(
    r"data-job-url=(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))",
    re.IGNORECASE,
)
_BLOCK_TAG = re.compile(r"</(?:p|div|h[1-6]|li|tr|ul)\s*>|<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WORK_TYPE = {
    "remote": re.compile(r"\bremote\b", re.IGNORECASE),
    "hybrid": re.compile(r"\bhybrid\b", re.IGNORECASE),
    "onsite": re.compile(r"\bon-?site\b", re.IGNORECASE),
}


class EuroTechJobsAdapter:
    """Adapter ``eurotechjobs`` for the keyword search and the remote category."""

    id = "eurotechjobs"

    def __init__(
        self,
        *,
        database_path: str | Path | None = None,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.database_path = database_path
        self.output = output_fn

    def discover(
        self,
        page: Any,
        site: Site,
        query: str | None,
        location: str | None,
        limit: int,
        filters: SearchFilters,
    ) -> list[JobState]:
        """Collect unseen jobs from one EuroTechJobs results page."""

        del location
        if limit < 1:
            raise ValueError("limit must be at least 1")
        listing_url = _listing_url(site, query)
        if listing_url is None:
            self.output(f"EuroTechJobs {site.id} has no keyword to search.")
            return []
        self.output(f"EuroTechJobs: reading {listing_url}")
        html = self._read(page, listing_url)
        if html is None:
            self.output(f"EuroTechJobs {site.id} could not be read.")
            return []
        cards = _cards(html)
        if not cards:
            self.output(f"EuroTechJobs {site.id} returned no jobs.")
            return []

        max_age = _max_age_seconds(filters)
        wanted_work = _wanted_work_type(site, filters)
        jobs: list[JobState] = []
        for card in cards:
            if wanted_work is not None and not _text_has_work_type(card["text"], wanted_work):
                continue
            posted_at = _published_at(card["posted"])
            if posted_too_old(posted_at, max_age):
                continue
            url = canonicalize_url(urljoin(listing_url, card["href"]))
            source_job_id = card["source_job_id"]
            if seen(
                site.name,
                source_job_id,
                url,
                path=self.database_path,
                site_id=site.id,
            ):
                continue
            detail_html = self._read(page, url)
            description, apply_url = _detail_fields(detail_html or "", url)
            if not description:
                description = card["excerpt"]
            if not card["title"] or not card["company"] or not description:
                continue
            state = JobState(
                url=url,
                source_job_id=source_job_id,
                title=card["title"],
                company=card["company"],
                location=card["location"],
                site=site.name,
                site_id=site.id,
                raw_description=description,
                easy_apply=False,
                apply_url=apply_url,
                attempts=0,
            )
            if seen(
                site.name,
                source_job_id,
                url,
                path=self.database_path,
                site_id=site.id,
                apply_url=apply_url,
                company=card["company"],
                title=card["title"],
                location=card["location"],
            ):
                continue
            upsert_job(state, path=self.database_path)
            jobs.append(state)
            self.output(
                f"  Found [{len(jobs)}/{limit}]: {card['title'][:70]}"
            )
            if len(jobs) >= limit:
                break
        return jobs

    def _read(self, page: Any, url: str) -> str | None:
        request = getattr(page, "request", None)
        get = getattr(request, "get", None)
        if get is None:
            self.output(f"EuroTechJobs has no browser request client for {url}.")
            return None
        try:
            response = get(url, timeout=60_000)
            status = int(getattr(response, "status", 0) or 0)
            if not getattr(response, "ok", False):
                self.output(f"EuroTechJobs returned HTTP {status} for {url}.")
                return None
            body = response.text() if callable(getattr(response, "text", None)) else response.text
        except Exception as exc:
            self.output(f"EuroTechJobs could not read {url}: {exc}")
            return None
        if not isinstance(body, str) or not body.strip():
            return None
        return body


def _listing_url(site: Site, query: str | None) -> str | None:
    if "{query_encoded}" not in site.url:
        return site.url
    token = quote_plus((query or "").strip())
    if not token:
        return None
    return site.url.format(query_encoded=token)


def _cards(html: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for inner in _CARD.findall(html):
        href_match = _JOB_HREF.search(inner)
        if href_match is None:
            continue
        title = _text(_H3.search(inner).group(1) if _H3.search(inner) else "")
        company = _text(_COMPANY.search(inner).group(1) if _COMPANY.search(inner) else "")
        location = _text(_LOCATION.search(inner).group(1) if _LOCATION.search(inner) else "")
        posted = _text(_POSTED.search(inner).group(1) if _POSTED.search(inner) else "")
        paragraphs = [_text(part) for part in _PARAGRAPH.findall(inner)]
        excerpt = "\n\n".join(part for part in paragraphs if part)
        cards.append(
            {
                "href": href_match.group(1),
                "source_job_id": href_match.group(2),
                "title": title,
                "company": company,
                "location": location,
                "posted": posted,
                "excerpt": excerpt,
                "text": _text(inner),
            }
        )
    return cards


def _detail_fields(html: str, listing_url: str) -> tuple[str, str]:
    display = _DISPLAY.search(html)
    description = _html_to_text(display.group(1)) if display else ""
    apply_match = _APPLY.search(html)
    raw_apply = ""
    if apply_match is not None:
        raw_apply = next(part for part in apply_match.groups() if part)
    apply_url = _apply_url(listing_url, raw_apply)
    return description, apply_url


def _apply_url(listing_url: str, raw_apply: str) -> str:
    absolute = urljoin(listing_url, raw_apply.strip()) if raw_apply.strip() else ""
    host = absolute.casefold()
    if absolute.startswith("http") and "eurotechjobs.com/job/track_click" not in host:
        return absolute
    return listing_url


def _wanted_work_type(site: Site, filters: SearchFilters) -> str | None:
    if site.work_type != "post_filter":
        return None
    value = str(filters.get("work_type") or "").strip().casefold()
    if not value or value == "any":
        return None
    return value


def _text_has_work_type(text: str, work_type: str) -> bool:
    pattern = _WORK_TYPE.get(work_type)
    if pattern is None:
        return True
    return pattern.search(text) is not None


def _published_at(text: str) -> datetime | None:
    if not text.strip():
        return None
    if re.search(r"\btoday\b", text, re.IGNORECASE):
        return datetime.now(timezone.utc)
    if re.search(r"\byesterday\b", text, re.IGNORECASE):
        return datetime.now(timezone.utc) - timedelta(days=1)
    return parse_posted_at(text)


def _max_age_seconds(filters: SearchFilters) -> int | None:
    if "month" not in filters or filters.get("month") is None:
        return None
    return posted_window_seconds(month=int(filters["month"]))


def _text(value: str) -> str:
    return _html_to_text(value)


def _html_to_text(value: str) -> str:
    if not value.strip():
        return ""
    text = _BLOCK_TAG.sub("\n", value)
    text = _TAG.sub("", text)
    return clean_job_text(unescape(text))
