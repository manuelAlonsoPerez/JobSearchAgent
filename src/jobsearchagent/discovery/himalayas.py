"""Himalayas discovery. One adapter serves the ``himalayas`` registry row.

The public jobs API returns the same fields a detail page would: title,
company, description, apply link, country allow-list, and publish date.
The category page itself answers 403 to a plain fetch, so discovery reads
``/jobs/api`` through the Playwright request context and does not open
linkedin.com.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from ..config import Site
from ..database import seen, upsert_job
from ..models import JobState
from ..utils.text import canonicalize_url, clean_job_text
from .linkedin import posted_too_old, posted_window_seconds

if TYPE_CHECKING:
    from .registry import SearchFilters


PAGE_SIZE = 20
MAX_PAGES = 30
_ALLOWED_COUNTRIES = {"norway", "spain", "españa"}
_WORLDWIDE = {
    "worldwide",
    "anywhere",
    "anywhere in the world",
    "global",
    "work from anywhere",
    "all countries",
    "no restriction",
    "unrestricted",
}
_BLOCK_TAG = re.compile(r"</(?:p|div|h[1-6]|li|tr)\s*>|<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


class HimalayasAdapter:
    """Adapter ``himalayas``. ``query`` and ``location`` are ignored."""

    id = "himalayas"

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
        """Collect unseen developer jobs open to Norway, Spain, or worldwide."""

        del query, location
        if limit < 1:
            raise ValueError("limit must be at least 1")

        jobs: list[JobState] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        max_age = _max_age_seconds(filters)
        category = _category_slug(site)

        for page_index in range(MAX_PAGES):
            api_url = _api_url(site, cursor)
            payload = self._read_feed(page, site, api_url)
            if payload is None:
                return jobs

            listings = payload.get("jobs")
            if page_index == 0 and not listings:
                self.output(f"Himalayas {site.id} returned no jobs.")
                return []
            if not isinstance(listings, list):
                self.output(f"Himalayas {site.id} returned no jobs.")
                return jobs

            stop_for_age = False
            for listing in listings:
                if not isinstance(listing, dict):
                    continue
                posted_at = _published_at(listing.get("pubDate"))
                if posted_too_old(posted_at, max_age):
                    stop_for_age = True
                    continue
                state = _job_state(listing, site, category)
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
                    f"  Found [{len(jobs)}/{limit}]: "
                    f"{str(state.get('title', '')).strip()[:70]}"
                )
                if len(jobs) >= limit:
                    return jobs

            if stop_for_age:
                return jobs
            next_cursor = payload.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                return jobs
            if next_cursor in seen_cursors:
                return jobs
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        self.output(
            f"Himalayas {site.id} stopped after {MAX_PAGES} pages "
            f"with {len(jobs)} new jobs."
        )
        return jobs

    def _read_feed(self, page: Any, site: Site, api_url: str) -> dict[str, Any] | None:
        self.output(f"Himalayas: reading {api_url}")
        request = getattr(page, "request", None)
        get = getattr(request, "get", None)
        if get is None:
            self.output(f"Himalayas {site.id} has no browser request client.")
            return None
        try:
            response = get(api_url, timeout=60_000)
            status = int(getattr(response, "status", 0) or 0)
            if not getattr(response, "ok", False):
                self.output(
                    f"Himalayas {site.id} returned HTTP {status}; no jobs collected."
                )
                return None
            payload = response.json()
        except Exception as exc:
            self.output(f"Himalayas {site.id} could not be read: {exc}")
            return None
        if not isinstance(payload, dict):
            self.output(f"Himalayas {site.id} returned no jobs.")
            return None
        return payload


def _job_state(
    listing: dict[str, Any],
    site: Site,
    category: str | None,
) -> JobState | None:
    if category is not None and not _matches_category(listing, category):
        return None
    restrictions = _restrictions(listing)
    if not _country_is_open(restrictions):
        return None

    guid = str(listing.get("guid") or "").strip()
    apply_url = str(listing.get("applicationLink") or "").strip()
    listing_url = guid if guid.startswith("http") else apply_url
    if not listing_url.startswith("http"):
        return None

    title = str(listing.get("title") or "").strip()
    company = str(listing.get("companyName") or "").strip()
    description = _html_to_text(str(listing.get("description") or ""))
    if not description:
        description = _html_to_text(str(listing.get("excerpt") or ""))
    if not title or not company or not description:
        return None

    return JobState(
        url=canonicalize_url(listing_url),
        source_job_id=guid,
        title=title,
        company=company,
        location=_location_text(restrictions),
        employment_type=str(listing.get("employmentType") or "").strip(),
        site=site.name,
        site_id=site.id,
        raw_description=description,
        easy_apply=False,
        apply_url=apply_url or canonicalize_url(listing_url),
        attempts=0,
    )


def _api_url(site: Site, cursor: str | None) -> str:
    parts = urlsplit(site.url)
    query: dict[str, str] = {"limit": str(PAGE_SIZE)}
    if cursor:
        query["cursor"] = cursor
    return urlunsplit(
        (
            parts.scheme or "https",
            parts.netloc,
            "/jobs/api",
            urlencode(query),
            "",
        )
    )


def _category_slug(site: Site) -> str | None:
    parts = [part for part in urlsplit(site.url).path.split("/") if part]
    if len(parts) >= 2 and parts[0].casefold() == "jobs":
        return parts[1].casefold()
    return None


def _matches_category(listing: dict[str, Any], slug: str) -> bool:
    labels = list(listing.get("categories") or []) + list(
        listing.get("parentCategories") or []
    )
    for label in labels:
        pieces = str(label).strip().casefold().replace("_", "-").split("-")
        if slug in pieces:
            return True
    return False


def _restrictions(listing: dict[str, Any]) -> list[str]:
    raw = listing.get("locationRestrictions") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _country_is_open(restrictions: list[str]) -> bool:
    names = {item.casefold() for item in restrictions}
    if names & _WORLDWIDE:
        return True
    return bool(names & _ALLOWED_COUNTRIES)


def _location_text(restrictions: list[str]) -> str:
    if any(item.casefold() in _WORLDWIDE for item in restrictions):
        return "Worldwide"
    preferred = [item for item in restrictions if item.casefold() in _ALLOWED_COUNTRIES]
    others = [item for item in restrictions if item.casefold() not in _ALLOWED_COUNTRIES]
    shown = preferred + others[:6]
    extra = len(restrictions) - len(shown)
    text = ", ".join(shown)
    if extra > 0:
        text = f"{text}, and {extra} more"
    return text


def _published_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
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
        try:
            value = float(text)
        except ValueError:
            return None
    try:
        number = float(value)
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
