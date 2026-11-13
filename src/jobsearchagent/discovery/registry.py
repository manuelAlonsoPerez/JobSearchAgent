"""Discovery adapter registry.

One slug from ``sites.yaml`` maps to one :class:`SiteAdapter`. LinkedIn stays
in :mod:`jobsearchagent.discovery.linkedin`; this module only names it and
translates the shared filter dict into the filters that scraper already uses.
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict

from ..config import Site, refuse_linkedin_automation
from ..models import JobState
from .arbeitnow import ArbeitnowAdapter
from .eurotechjobs import EuroTechJobsAdapter
from .himalayas import HimalayasAdapter
from .remoteok import RemoteOKAdapter
from .remotive import RemotiveAdapter
from .working_nomads import WorkingNomadsAdapter
from .wwr import WeWorkRemotelyAdapter
from .linkedin import (
    WORK_TYPE_CODES,
    LinkedInDiscovery,
    SearchFilters as LinkedInSearchFilters,
    posted_window_seconds,
)


class SearchFilters(TypedDict, total=False):
    """Shared filters passed to every adapter.

    ``month`` and ``work_type`` are the CLI words (``remote``, ``any``).
    ``easy_apply`` and ``geo_id`` are read only by the LinkedIn adapter.
    A missing key means "use that adapter's own default".
    """

    month: int | None
    work_type: str | None
    easy_apply: bool
    geo_id: str | None


class SiteAdapter(Protocol):
    """One board's discovery implementation."""

    id: str

    def discover(
        self,
        page: Any,
        site: Site,
        query: str | None,
        location: str | None,
        limit: int,
        filters: SearchFilters,
    ) -> list[JobState]:
        """Return normalized jobs. ``query`` and ``location`` are ``None`` when skipped."""


class LinkedInAdapter:
    """Adapter ``linkedin``. Search behaviour stays in :class:`LinkedInDiscovery`."""

    id = "linkedin"

    def discover(
        self,
        page: Any,
        site: Site,
        query: str | None,
        location: str | None,
        limit: int,
        filters: SearchFilters,
    ) -> list[JobState]:
        refuse_linkedin_automation()
        discovery = LinkedInDiscovery()
        jobs = discovery.discover_with_page(
            page,
            query or "",
            location or "",
            limit,
            filters=linkedin_search_filters(filters),
        )
        for job in jobs:
            job["site"] = site.name
        return jobs


_ADAPTERS: dict[str, SiteAdapter] = {}


def register_adapter(adapter: SiteAdapter) -> None:
    """Add or replace the adapter stored under ``adapter.id``."""

    slug = str(adapter.id).strip()
    if not slug:
        raise ValueError("An adapter id is required")
    _ADAPTERS[slug] = adapter


def adapter_for(slug: str, *, site_id: str | None = None) -> SiteAdapter:
    """Return the adapter for a slug. An unknown slug names the site id."""

    try:
        return _ADAPTERS[slug]
    except KeyError as exc:
        site = f" for site {site_id}" if site_id else ""
        raise RuntimeError(
            f"No discovery adapter registered for slug '{slug}'{site}."
        ) from exc


def require_adapters(sites: list[Site]) -> dict[str, SiteAdapter]:
    """Resolve each site's adapter. The first unknown slug stops the run."""

    resolved: dict[str, SiteAdapter] = {}
    missing: list[str] = []
    for site in sites:
        adapter = _ADAPTERS.get(site.adapter)
        if adapter is None:
            missing.append(f"{site.id} (adapter {site.adapter})")
            continue
        resolved[site.id] = adapter
    if missing:
        raise RuntimeError(
            "No discovery adapter registered for: " + ", ".join(missing) + "."
        )
    return resolved


def discover_with_adapter(
    adapter: SiteAdapter,
    page: Any,
    site: Site,
    query: str | None,
    location: str | None,
    limit: int,
    filters: SearchFilters | None = None,
) -> list[JobState]:
    """Call an adapter and apply the shared job-state rules.

    Every row is labeled with ``site.name``. Adapters other than LinkedIn
    cannot set ``easy_apply``.
    """

    jobs = adapter.discover(page, site, query, location, limit, filters or SearchFilters())
    for job in jobs:
        job["site"] = site.name
        if adapter.id != "linkedin":
            job["easy_apply"] = False
    return jobs


def linkedin_search_filters(filters: SearchFilters | None) -> LinkedInSearchFilters:
    """Map the shared filter dict onto the LinkedIn URL builder's filters.

    Omitted keys keep :class:`~jobsearchagent.discovery.linkedin.SearchFilters`
    defaults, so an empty dict still means remote, Easy Apply, last week.
    """

    incoming = filters or SearchFilters()
    values: dict[str, Any] = {}
    if "month" in incoming:
        month = incoming.get("month")
        values["posted_within_seconds"] = (
            None if month is None else posted_window_seconds(month=int(month))
        )
    if "work_type" in incoming:
        values["work_type"] = _linkedin_work_type(incoming.get("work_type"))
    if "easy_apply" in incoming:
        values["easy_apply"] = bool(incoming.get("easy_apply"))
    if "geo_id" in incoming:
        geo_id = incoming.get("geo_id")
        values["geo_id"] = None if geo_id is None else str(geo_id)
    return LinkedInSearchFilters(**values)


def _linkedin_work_type(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() == "any":
        return None
    code = WORK_TYPE_CODES.get(text.casefold())
    return code if code is not None else text


register_adapter(LinkedInAdapter())
register_adapter(HimalayasAdapter())
register_adapter(EuroTechJobsAdapter())
register_adapter(RemoteOKAdapter())
register_adapter(WeWorkRemotelyAdapter())
register_adapter(RemotiveAdapter())
register_adapter(WorkingNomadsAdapter())
register_adapter(ArbeitnowAdapter())
