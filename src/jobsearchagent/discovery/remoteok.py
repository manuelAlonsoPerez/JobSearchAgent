"""RemoteOK discovery. One adapter serves the ``remoteok`` registry row.

The developer category page is a shell. The page links an alternate JSON
feed at the same path plus ``.json``, and that feed is newest-first. Discovery
reads it through the Playwright request context. ``--month`` drops listings
once the feed reaches an older date. A missing date is kept.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Site
from ..database import seen, upsert_job
from ..models import JobState
from ..utils.text import canonicalize_url, clean_job_text
from .linkedin import posted_too_old, posted_window_seconds

if TYPE_CHECKING:
    from .registry import SearchFilters


_BLOCK_TAG = re.compile(r"</(?:p|div|h[1-6]|li|tr|ul)\s*>|<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


class RemoteOKAdapter:
    """Adapter ``remoteok``. ``query`` and ``location`` are ignored."""

    id = "remoteok"

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
        """Collect unseen jobs from the RemoteOK JSON feed."""

        del query, location
        if limit < 1:
            raise ValueError("limit must be at least 1")
        feed_url = _feed_url(site)
        self.output(f"RemoteOK: reading {feed_url}")
        payload = self._read_feed(page, site, feed_url)
        if payload is None:
            return []
        listings = [item for item in payload if isinstance(item, dict) and item.get("id")]
        if not listings:
            self.output(f"RemoteOK {site.id} returned no jobs.")
            return []

        max_age = _max_age_seconds(filters)
        jobs: list[JobState] = []
        for listing in listings:
            posted_at = _published_at(listing)
            if posted_too_old(posted_at, max_age):
                return jobs
            state = _job_state(listing, site)
            if state is None:
                continue
            url = str(state["url"])
            source_job_id = str(state.get("source_job_id") or "")
            if seen(
                site.name,
                source_job_id,
                url,
                path=self.database_path,
                site_id=site.id,
                apply_url=str(state.get("apply_url") or ""),
                company=str(state.get("company") or ""),
                title=str(state.get("title") or ""),
                location=str(state.get("location") or ""),
            ):
                continue
            upsert_job(state, path=self.database_path)
            jobs.append(state)
            self.output(
                f"  Found [{len(jobs)}/{limit}]: {str(state.get('title', '')).strip()[:70]}"
            )
            if len(jobs) >= limit:
                break
        return jobs

    def _read_feed(self, page: Any, site: Site, feed_url: str) -> list[Any] | None:
        request = getattr(page, "request", None)
        get = getattr(request, "get", None)
        if get is None:
            self.output(f"RemoteOK {site.id} has no browser request client.")
            return None
        try:
            response = get(feed_url, timeout=60_000)
            status = int(getattr(response, "status", 0) or 0)
            if not getattr(response, "ok", False):
                self.output(f"RemoteOK {site.id} returned HTTP {status}; no jobs collected.")
                return None
            payload = response.json() if callable(getattr(response, "json", None)) else response.json
        except Exception as exc:
            self.output(f"RemoteOK {site.id} could not be read: {exc}")
            return None
        if not isinstance(payload, list):
            self.output(f"RemoteOK {site.id} returned no jobs.")
            return None
        return payload


def _feed_url(site: Site) -> str:
    url = site.url.strip()
    if url.casefold().endswith(".json"):
        return url
    return url.rstrip("/") + ".json"


def _job_state(listing: dict[str, Any], site: Site) -> JobState | None:
    source_job_id = str(listing.get("id") or "").strip()
    title = str(listing.get("position") or "").strip()
    company = str(listing.get("company") or "").strip()
    description = _html_to_text(str(listing.get("description") or ""))
    raw_url = str(listing.get("url") or listing.get("apply_url") or "").strip()
    if not source_job_id or not title or not company or not description:
        return None
    if not raw_url.startswith("http"):
        return None
    listing_url = canonicalize_url(raw_url)
    apply_raw = str(listing.get("apply_url") or raw_url).strip()
    apply_url = canonicalize_url(apply_raw) if apply_raw.startswith("http") else listing_url
    return JobState(
        url=listing_url,
        source_job_id=source_job_id,
        title=title,
        company=company,
        location=str(listing.get("location") or "").strip(),
        site=site.name,
        site_id=site.id,
        raw_description=description,
        easy_apply=False,
        apply_url=apply_url,
        attempts=0,
    )


def _published_at(listing: dict[str, Any]) -> datetime | None:
    text = str(listing.get("date") or "").strip()
    if text:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
    raw = listing.get("epoch")
    if raw is None or raw == "":
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number /= 1000
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _max_age_seconds(filters: SearchFilters) -> int | None:
    if "month" not in filters or filters.get("month") is None:
        return None
    return posted_window_seconds(month=int(filters["month"]))


def _html_to_text(value: str) -> str:
    if not value.strip():
        return ""
    text = _BLOCK_TAG.sub("\n", value)
    text = _TAG.sub("", text)
    return clean_job_text(unescape(text))
