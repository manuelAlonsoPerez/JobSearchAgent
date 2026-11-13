"""We Work Remotely discovery. One adapter serves the three category rows.

Each category page links an RSS feed at the same path plus ``.rss``. The feed
already contains the description, so discovery does not open each posting.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
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


_ITEM = re.compile(r"<item>(.*?)</item>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<([a-zA-Z0-9:]+)>(.*?)</\1>", re.DOTALL)


class WeWorkRemotelyAdapter:
    """Adapter ``wwr``. ``query`` and ``location`` are ignored."""

    id = "wwr"

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
        feed_url = site.url.strip().rstrip("/") + ".rss"
        self.output(f"We Work Remotely: reading {feed_url}")
        payload = read_response(page, feed_url, self.output, f"We Work Remotely {site.id}")
        if not isinstance(payload, str):
            self.output(f"We Work Remotely {site.id} returned no jobs.")
            return []
        items = _items(payload)
        if not items:
            self.output(f"We Work Remotely {site.id} returned no jobs.")
            return []
        return _collect(self, site, items, limit, max_age_seconds(filters))


def _items(xml: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for block in _ITEM.findall(xml):
        fields = {name.casefold(): unescape(value).strip() for name, value in _TAG.findall(block)}
        if fields.get("link") or fields.get("guid"):
            items.append(fields)
    return items


def _collect(
    adapter: WeWorkRemotelyAdapter,
    site: Site,
    items: list[dict[str, str]],
    limit: int,
    max_age: int | None,
) -> list[JobState]:
    jobs: list[JobState] = []
    for item in items:
        posted_at = _published_at(item.get("pubdate", ""))
        if posted_too_old(posted_at, max_age):
            break
        state = _job_state(item, site)
        if state is None:
            continue
        if _already_seen(adapter, site, state):
            continue
        upsert_job(state, path=adapter.database_path)
        jobs.append(state)
        adapter.output(f"  Found [{len(jobs)}/{limit}]: {state['title'][:70]}")
        if len(jobs) >= limit:
            break
    return jobs


def _job_state(item: dict[str, str], site: Site) -> JobState | None:
    raw_url = (item.get("link") or item.get("guid") or "").strip()
    if not raw_url.startswith("http"):
        return None
    company, title = _split_title(item.get("title", ""))
    description = html_to_text(item.get("description", ""))
    if not company or not title or not description:
        return None
    listing_url = canonicalize_url(raw_url)
    source_job_id = urlsplit(listing_url).path.rstrip("/").split("/")[-1]
    return JobState(
        url=listing_url,
        source_job_id=source_job_id,
        title=title,
        company=company,
        location=(item.get("region") or "").strip(),
        employment_type=(item.get("type") or "").strip(),
        site=site.name,
        site_id=site.id,
        raw_description=description,
        easy_apply=False,
        apply_url=listing_url,
        attempts=0,
    )


def _split_title(value: str) -> tuple[str, str]:
    company, separator, title = value.partition(":")
    if not separator:
        return "", ""
    return company.strip(), title.strip()


def _published_at(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _already_seen(adapter: WeWorkRemotelyAdapter, site: Site, state: JobState) -> bool:
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
