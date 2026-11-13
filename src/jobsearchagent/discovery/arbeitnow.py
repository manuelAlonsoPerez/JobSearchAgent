"""Arbeitnow discovery for the remote row.

The public job-board API is paged and newest-first. The remote row keeps
listings whose ``remote`` flag is true.
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


_API = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 3


class ArbeitnowAdapter:
    """Adapter ``arbeitnow``. ``query`` and ``location`` are ignored."""

    id = "arbeitnow"

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
        max_age = max_age_seconds(filters)
        jobs: list[JobState] = []
        next_url: str | None = _API
        for _page_index in range(_MAX_PAGES):
            if next_url is None or len(jobs) >= limit:
                break
            self.output(f"Arbeitnow: reading {next_url}")
            payload = read_response(page, next_url, self.output, f"Arbeitnow {site.id}")
            if not isinstance(payload, dict):
                if not jobs:
                    self.output(f"Arbeitnow {site.id} returned no jobs.")
                return jobs
            listings = payload.get("data")
            if not isinstance(listings, list) or not listings:
                if not jobs:
                    self.output(f"Arbeitnow {site.id} returned no jobs.")
                return jobs
            stop = False
            for listing in listings:
                if not isinstance(listing, dict) or not listing.get("remote"):
                    continue
                posted_at = _published_at(listing.get("created_at"))
                if posted_too_old(posted_at, max_age):
                    stop = True
                    break
                state = _job_state(listing, site)
                if state is None or _seen(self, site, state):
                    continue
                upsert_job(state, path=self.database_path)
                jobs.append(state)
                self.output(f"  Found [{len(jobs)}/{limit}]: {state['title'][:70]}")
                if len(jobs) >= limit:
                    break
            if stop or len(jobs) >= limit:
                break
            link = (payload.get("links") or {}).get("next") if isinstance(payload.get("links"), dict) else None
            next_url = link if isinstance(link, str) and link.startswith("http") else None
        return jobs


def _job_state(listing: dict[str, Any], site: Site) -> JobState | None:
    raw_url = str(listing.get("url") or "").strip()
    title = str(listing.get("title") or "").strip()
    company = str(listing.get("company_name") or "").strip()
    description = html_to_text(str(listing.get("description") or ""))
    source_job_id = str(listing.get("slug") or "").strip()
    if not raw_url.startswith("http") or not title or not company or not description or not source_job_id:
        return None
    listing_url = canonicalize_url(raw_url)
    job_types = listing.get("job_types") or []
    employment = job_types[0] if isinstance(job_types, list) and job_types else ""
    return JobState(
        url=listing_url,
        source_job_id=source_job_id,
        title=title,
        company=company,
        location=str(listing.get("location") or "").strip(),
        employment_type=str(employment).strip(),
        site=site.name,
        site_id=site.id,
        raw_description=description,
        easy_apply=False,
        apply_url=listing_url,
        attempts=0,
    )


def _published_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number /= 1000
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _seen(adapter: ArbeitnowAdapter, site: Site, state: JobState) -> bool:
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
