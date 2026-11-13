"""Remotive discovery for the software category.

The category page asks for an account before it shows every job. The public
JSON feed is what discovery reads. Jobs outside software, IT, and AI are
dropped, because that feed still mixes in other categories.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Site
from ..database import seen, upsert_job
from ..models import JobState
from ..utils.text import canonicalize_url
from .fetch import html_to_text, max_age_seconds, read_response
from .linkedin import posted_too_old

if TYPE_CHECKING:
    from .registry import SearchFilters


_API = "https://remotive.com/api/remote-jobs?category=software-dev"
_SOFTWARE = {"software development", "information technology", "artificial intelligence"}


class RemotiveAdapter:
    """Adapter ``remotive``. ``query`` and ``location`` are ignored."""

    id = "remotive"

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
        del query, location
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.output(f"Remotive: reading {_API}")
        payload = read_response(page, _API, self.output, f"Remotive {site.id}")
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list) or not jobs:
            self.output(f"Remotive {site.id} returned no jobs.")
            return []
        max_age = max_age_seconds(filters)
        saved: list[JobState] = []
        for listing in jobs:
            if not isinstance(listing, dict):
                continue
            if not _matches_site(site, listing):
                continue
            posted_at = _published_at(str(listing.get("publication_date") or ""))
            if posted_too_old(posted_at, max_age):
                continue
            state = _job_state(listing, site)
            if state is None or _seen(self, site, state):
                continue
            upsert_job(state, path=self.database_path)
            saved.append(state)
            self.output(f"  Found [{len(saved)}/{limit}]: {state['title'][:70]}")
            if len(saved) >= limit:
                break
        return saved


def _matches_site(site: Site, listing: dict[str, Any]) -> bool:
    if "software" not in site.url.casefold():
        return True
    return str(listing.get("category") or "").strip().casefold() in _SOFTWARE


def _job_state(listing: dict[str, Any], site: Site) -> JobState | None:
    raw_url = str(listing.get("url") or "").strip()
    title = str(listing.get("title") or "").strip()
    company = str(listing.get("company_name") or "").strip()
    description = html_to_text(str(listing.get("description") or ""))
    source_job_id = str(listing.get("id") or "").strip()
    if not raw_url.startswith("http") or not title or not company or not description or not source_job_id:
        return None
    listing_url = canonicalize_url(raw_url)
    return JobState(
        url=listing_url,
        source_job_id=source_job_id,
        title=title,
        company=company,
        location=str(listing.get("candidate_required_location") or "").strip(),
        employment_type=str(listing.get("job_type") or "").strip(),
        site=site.name,
        site_id=site.id,
        raw_description=description,
        easy_apply=False,
        apply_url=listing_url,
        attempts=0,
    )


def _published_at(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seen(adapter: RemotiveAdapter, site: Site, state: JobState) -> bool:
    return seen(
        site.name,
        str(state.get("source_job_id") or ""),
        str(state["url"]),
        path=adapter.database_path,
        site_id=site.id,
        apply_url=str(state.get("apply_url") or ""),
        company=str(state.get("company") or ""),
        title=str(state.get("title") or ""),
        location=str(state.get("location") or ""),
    )
