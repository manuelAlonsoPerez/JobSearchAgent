"""Search units for the multi-site run.

A static board, or one that cannot take a query, is a single unit. A board
that cannot take a location does not repeat the unit per place. Token queries
are rewritten here so each adapter does not grow its own copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import Site

_TOKEN = re.compile(r"[A-Za-z0-9]+")
_TOKEN_PHRASES = {
    "servicenow developer": "servicenow",
    "full stack developer": "fullstack",
    "fullstack developer": "fullstack",
}


@dataclass(frozen=True)
class SearchUnit:
    """One discovery call: a site already chosen, plus the axes it supports."""

    query: str | None
    location: str | None


def query_text(phrase: str, style: str) -> str:
    """Return the phrase, or the token a path-style board can put in a URL."""

    text = phrase.strip()
    if style != "token":
        return text
    mapped = _TOKEN_PHRASES.get(text.casefold())
    if mapped:
        return mapped
    match = _TOKEN.search(text)
    return match.group(0).casefold() if match else text.casefold()


def search_units(
    site: Site,
    queries: list[str],
    locations: list[str],
) -> list[SearchUnit]:
    """Expand one site into the query and location pairs it actually runs."""

    if site.type == "static" or not site.supports_query:
        phrases: list[str | None] = [None]
    else:
        phrases = [query_text(query, site.query_style) for query in queries]
    if site.supports_location:
        places: list[str | None] = list(locations) or [None]
    else:
        places = [None]
    return [
        SearchUnit(query=query, location=location)
        for query in phrases
        for location in places
    ]


def skip_reason(
    site: Site,
    *,
    month: int | None,
    work_type: str | None,
) -> str | None:
    """Why this run must not scrape the site, or ``None`` when it may run."""

    if month is not None and site.posted_within == "unsupported":
        return (
            f"{site.id} skipped: posted_within is unsupported, "
            f"so --month {month} would not limit this board."
        )
    if work_type and work_type != "any" and site.work_type == "unsupported":
        return (
            f"{site.id} skipped: work_type is unsupported, "
            f"so --work-type {work_type} cannot be applied."
        )
    return None


def select_sites(
    sites: list[Site],
    only: list[str] | None,
    skip: list[str] | None,
) -> tuple[list[Site], list[str]]:
    """Intersect ``--site`` with the enabled rows, then apply ``--skip-site``."""

    chosen = list(sites)
    missing: list[str] = []
    if only:
        wanted: list[str] = []
        seen: set[str] = set()
        for item in only:
            text = str(item).strip()
            if text and text not in seen:
                wanted.append(text)
                seen.add(text)
        by_id = {site.id: site for site in chosen}
        wanted_ids = set(wanted)
        chosen = [site for site in chosen if site.id in wanted_ids]
        missing = [item for item in wanted if item not in by_id]
    if skip:
        blocked = {str(item).strip() for item in skip if str(item).strip()}
        chosen = [site for site in chosen if site.id not in blocked]
    return chosen, missing
