"""Working Nomads discovery for the development category.

The public JSON feed lists every category. The development row keeps
``category_name`` Development.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..config import Site
from ..database import seen, upsert_job
from ..models import JobState
from ..utils.text import canonicalize_url
from .fetch import html_to_text, max_age_seconds, read_response
from .linkedin import posted_too_old

if TYPE_CHECKING:
    from .registry import SearchFilters


_API = "https://www.workingnomads.com/api/exposed_jobs"


class WorkingNomadsAdapter:
    """Adapter ``working_nomads``. ``query`` and ``location`` are ignored."""

    id = "working_nomads"

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
        self.output(f"Working Nomads: reading {_API}")
        payload = read_response(page, _API, self.output, f"Working Nomads {site.id}")
        if not isinstance(payload, list) or not payload:
            self.output(f"Working Nomads {site.id} returned no jobs.")
            return []
        max_age = max_age_seconds(filters)
        jobs: list[JobState] = []
        for listing in payload:
            if not isinstance(listing, dict):
                continue
            category = str(listing.get("category_name") or "").casefold()
            if "development" in site.url.casefold() and category != "development":
                continue
            posted_at = _published_at(str(listing.get("pub_date") or ""))
            if posted_too_old(posted_at, max_age):
                continue
            state = _job_state(listing, site)
            if state is None or _seen(self, site, state):
                continue
            upsert_job(state, path=self.database_path)
            jobs.append(state)
            self.output(f"  Found [{len(jobs)}/{limit}]: {state['title'][:70]}")
            if len(jobs) >= limit:
                break
        return jobs


def _job_state(listing: dict[str, Any], site: Site) -> JobState | None:
    raw_url = str(listing.get("url") or "").strip()
    title = str(listing.get("title") or "").strip()
    company = str(listing.get("company_name") or "").strip()
    description = html_to_text(str(listing.get("description") or ""))
    if not raw_url.startswith("http") or not title or not company or not description:
        return None
    listing_url = canonicalize_url(raw_url)
    parts = [part for part in urlsplit(listing_url).path.split("/") if part]
    source = parts[-1] if parts else listing_url
    return JobState(
        url=listing_url,
        source_job_id=source,
        title=title,
        company=company,
        location=str(listing.get("location") or "").strip(),
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
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _seen(adapter: WorkingNomadsAdapter, site: Site, state: JobState) -> bool:
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
