"""Authenticated LinkedIn discovery producing normalized ``JobState`` seeds."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from ..browser import session
from ..config import get_settings, linkedin_search_url, load_searches
from ..database import seen, upsert_job
from ..models import JobState
from ..utils.job_meta import enrich_job_metadata
from ..utils.text import (
    canonicalize_url,
    clean_job_text,
    extract_linkedin_job_id,
)

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


SITE = "LinkedIn Jobs"
_EASY_APPLY = re.compile(
    r"easy apply|solicitud sencilla|enkel søknad|candidatura simplificada",
    re.IGNORECASE,
)
_APPLY = re.compile(
    r"apply|solicitar|søknad|candidatura",
    re.IGNORECASE,
)
_LOGIN_MARKERS = ("/login", "/checkpoint", "/challenge")

# LinkedIn search-filter query-parameter codes.
POSTED_WITHIN_SECONDS: dict[str, int] = {
    "24h": 86_400,
    "week": 604_800,
    "month": 2_592_000,
}
WORK_TYPE_CODES: dict[str, str] = {
    "onsite": "1",
    "remote": "2",
    "hybrid": "3",
}
SORT_CODES: dict[str, str] = {
    "relevance": "R",
    "recent": "DD",
}


@dataclass(frozen=True)
class SearchFilters:
    """LinkedIn search filters applied on top of the configured base URL.

    Defaults reproduce the historical behavior (remote, Easy Apply, last week,
    by relevance). ``geo_id`` targets a LinkedIn region such as EMEA
    (``91000007``) and, when set, replaces the free-text ``location``.
    """

    geo_id: str | None = None
    posted_within_seconds: int | None = POSTED_WITHIN_SECONDS["week"]
    work_type: str | None = WORK_TYPE_CODES["remote"]
    easy_apply: bool = True
    sort: str | None = SORT_CODES["relevance"]


class LinkedInDiscovery:
    """Discover LinkedIn jobs with one persistent authenticated page."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        searches: Mapping[str, Any] | None = None,
    ) -> None:
        self.input = input_fn
        self.output = output_fn
        self.searches = searches or load_searches()
        self.page_limit = get_settings().linkedin_search_page_limit

    def discover_with_page(
        self,
        page: Page,
        query: str,
        location: str,
        limit: int = 20,
        *,
        filters: SearchFilters | None = None,
        stats: dict[str, int] | None = None,
    ) -> list[JobState]:
        """Search and normalize unseen jobs using an existing browser page."""

        if limit < 1:
            raise ValueError("limit must be at least 1")

        self._raise_for_security_challenge(page)
        candidates: list[str] = []
        candidate_seen: set[str] = set()
        for page_index in range(self.page_limit):
            search_url = self.build_search_url(
                query,
                location,
                start=page_index * 25,
                filters=filters,
            )
            self.output(f"Searching LinkedIn: {search_url}")
            self._navigate(page, search_url)
            self._raise_for_security_challenge(page)
            for url in self._collect_job_urls(page, max(limit * 2, 25)):
                if url not in candidate_seen:
                    candidate_seen.add(url)
                    candidates.append(url)
            if len(candidates) >= limit * 2:
                break

        jobs: list[JobState] = []
        for url in candidates:
            source_job_id = extract_linkedin_job_id(url) or ""
            if seen(SITE, source_job_id, url):
                if stats is not None:
                    stats["duplicate"] = int(stats.get("duplicate") or 0) + 1
                continue
            try:
                state = self._scrape_job(page, url)
            except (PlaywrightError, RuntimeError) as exc:
                self.output(f"Could not read {url}: {exc}")
                continue
            if self._title_is_excluded(str(state.get("title", ""))):
                continue
            upsert_job(state)
            jobs.append(state)
            self.output(
                f"  Found [{len(jobs)}/{limit}]: "
                f"{str(state.get('title', '')).strip()[:70]}"
            )
            if len(jobs) >= limit:
                break
        return jobs

    @staticmethod
    def build_search_url(
        query: str,
        location: str,
        *,
        start: int = 0,
        filters: SearchFilters | None = None,
    ) -> str:
        """Build a filtered LinkedIn search URL.

        With default ``filters`` this yields a recent, remote, Easy Apply URL
        sorted by relevance. ``filters.geo_id`` targets a region (e.g. EMEA)
        and replaces the free-text location.
        """

        filters = filters or SearchFilters()
        base = linkedin_search_url(query, location, easy_apply=filters.easy_apply)
        parts = urlsplit(base)
        parameters = dict(parse_qsl(parts.query, keep_blank_values=True))

        if filters.work_type:
            parameters["f_WT"] = filters.work_type
        else:
            parameters.pop("f_WT", None)

        if filters.posted_within_seconds:
            parameters["f_TPR"] = f"r{filters.posted_within_seconds}"
        else:
            parameters.pop("f_TPR", None)

        if filters.sort:
            parameters["sortBy"] = filters.sort

        if not filters.easy_apply:
            parameters.pop("f_AL", None)

        if filters.geo_id:
            # A region geoId drives the search; drop the free-text location.
            parameters["geoId"] = filters.geo_id
            parameters.pop("location", None)

        if start:
            parameters["start"] = str(start)
        else:
            parameters.pop("start", None)

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(parameters),
                "",
            )
        )

    def ensure_login(self, page: Page) -> None:
        """Reuse the persistent login, allowing only manual authentication."""

        self._navigate(page, "https://www.linkedin.com/feed/")
        if not self._requires_login(page):
            self.output("LinkedIn persistent profile is already logged in.")
            return

        self._navigate(page, "https://www.linkedin.com/login")
        self.output(
            "Complete LinkedIn login manually in the browser. Credentials are "
            "never read or stored by the agent."
        )
        self.input("Press Enter after LinkedIn shows your feed: ")
        self._navigate(page, "https://www.linkedin.com/feed/")
        if self._requires_login(page):
            raise RuntimeError(
                "LinkedIn login was not completed in the persistent profile."
            )

    def _title_is_excluded(self, title: str) -> bool:
        normalized = title.casefold()
        patterns = self.searches.get("exclude_titles", [])
        return any(
            str(pattern).casefold() in normalized
            for pattern in patterns
            if str(pattern).strip()
        )

    @staticmethod
    def _requires_login(page: Page) -> bool:
        url = page.url.casefold()
        if any(marker in url for marker in _LOGIN_MARKERS):
            return True
        return page.locator(
            'a[href*="/login"], input[name="session_key"]'
        ).count() > 0

    @staticmethod
    def _raise_for_security_challenge(page: Page) -> None:
        url = page.url.casefold()
        if "/checkpoint" in url or "/challenge" in url:
            raise RuntimeError(
                "LinkedIn presented a security challenge. Complete it manually "
                "before retrying; the agent will not automate challenges."
            )

    @staticmethod
    def _navigate(page: Page, url: str) -> None:
        last_error: PlaywrightError | None = None
        attempts = 3
        for attempt in range(attempts):
            try:
                page.goto(url, wait_until="commit", timeout=60_000)
                page.wait_for_function(
                    "() => document.body !== null",
                    timeout=30_000,
                )
                return
            except PlaywrightTimeoutError as exc:
                # Slow LinkedIn: if the document attached, proceed anyway.
                last_error = exc
                if page.locator("body").count():
                    return
            except PlaywrightError as exc:
                # Transient network blips (ERR_NETWORK_CHANGED,
                # ERR_CONNECTION_RESET, ERR_ABORTED, ...) are retryable; do not
                # trust a stale body from the previous page here.
                last_error = exc
            if attempt < attempts - 1:
                page.wait_for_timeout(1_500 * (attempt + 1))

        raise RuntimeError(
            f"LinkedIn did not produce a usable document after {attempts} "
            f"attempts while loading: {url}"
        ) from last_error

    @staticmethod
    def _collect_job_urls(page: Page, limit: int) -> list[str]:
        collected: dict[str, str] = {}
        for _ in range(8):
            links = page.locator('a[href*="/jobs/view/"]')
            for href in links.evaluate_all(
                "elements => elements.map(element => element.href)"
            ):
                if not isinstance(href, str):
                    continue
                job_id = extract_linkedin_job_id(href)
                if job_id and job_id not in collected:
                    collected[job_id] = canonicalize_url(href)
                    if len(collected) >= limit:
                        return list(collected.values())

            results = page.locator(".jobs-search-results-list").last
            if results.count():
                results.evaluate(
                    "element => element.scrollTop = element.scrollHeight"
                )
            else:
                page.evaluate(
                    """
                    () => {
                      const root = document.scrollingElement
                        ?? document.documentElement
                        ?? document.body;
                      if (root) window.scrollTo(0, root.scrollHeight);
                    }
                    """
                )
            page.wait_for_timeout(800)
        return list(collected.values())[:limit]

    def _scrape_job(self, page: Page, url: str) -> JobState:
        self._navigate(page, url)
        self._raise_for_security_challenge(page)
        try:
            page.locator(
                "#job-details, .jobs-description__content, "
                "[data-testid='expandable-text-box']"
            ).first.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeoutError:
            pass
        self._expand_description(page)

        fields = self._listing_fields(page)
        title = fields["title"]
        description = fields["description"]
        if not title or not description:
            raise RuntimeError("LinkedIn job title or description was not available")

        easy_apply, apply_url = self._application_details(page)
        canonical_url = canonicalize_url(url)
        return JobState(
            url=canonical_url,
            source_job_id=extract_linkedin_job_id(url) or "",
            title=title,
            company=fields["company"],
            location=fields["location"],
            employment_type=fields["employment_type"],
            site=SITE,
            raw_description=description,
            easy_apply=easy_apply,
            apply_url=apply_url or canonical_url,
            attempts=0,
        )

    @staticmethod
    def _application_details(page: Page) -> tuple[bool, str | None]:
        easy_apply = page.get_by_role("button", name=_EASY_APPLY).count() > 0
        if easy_apply:
            return True, None

        links = page.get_by_role("link", name=_APPLY)
        for index in range(links.count()):
            href = links.nth(index).get_attribute("href")
            if href and "linkedin.com/" not in href.casefold():
                return False, href
        return False, None

    @staticmethod
    def _expand_description(page: Page) -> None:
        names = re.compile(
            r"show more|see more|ver más|mostrar más|vis mer|"
            r"^\s*(?:…|\.\.\.)\s*more\s*$",
            re.IGNORECASE,
        )
        for matches in (
            page.get_by_role("button", name=names),
            page.get_by_text(names),
        ):
            control = matches.first
            if control.count() and control.is_visible():
                control.click()
                page.wait_for_timeout(300)
                return

    @classmethod
    def _listing_fields(cls, page: Page) -> dict[str, str]:
        """Read title, company, location, contract, and description from a job page."""

        snapshot = cls._page_listing_snapshot(page)
        company = cls._first_visible_text(
            page,
            (
                ".job-details-jobs-unified-top-card__company-name",
                ".jobs-unified-top-card__company-name",
                ".job-details-jobs-unified-top-card__primary-description-container "
                "a[href*='/company/']",
                "a[href*='/company/']",
                "a[href*='/school/']",
            ),
        ) or str(snapshot.get("companyFromLink") or "")
        title = cls._first_visible_text(
            page,
            (
                ".job-details-jobs-unified-top-card__job-title h1",
                ".job-details-jobs-unified-top-card__job-title",
                "h1",
            ),
        ) or str(snapshot.get("h1") or "")
        location = cls._first_visible_text(
            page,
            (
                ".job-details-jobs-unified-top-card__tertiary-description-container",
                ".jobs-unified-top-card__bullet",
                ".job-details-jobs-unified-top-card__primary-description-without-tagline",
                ".job-details-jobs-unified-top-card__primary-description-container",
            ),
        ) or cls._new_layout_location(page, title)
        description = cls._first_visible_text(
            page,
            (
                "#job-details",
                ".jobs-description__content",
                ".jobs-box__html-content",
            ),
        ) or cls._new_layout_description(page)
        recovered = enrich_job_metadata(
            {
                "title": title,
                "company": company,
                "location": location,
                "raw_description": description,
                "document_title": snapshot.get("documentTitle") or page.title(),
                "company_from_page": snapshot.get("companyFromLink"),
                "location_from_page": location,
                "employment_hint": "\n".join(snapshot.get("insights") or []),
                "json_ld": snapshot.get("jsonLd") or {},
            }
        )
        return {
            **recovered,
            "description": clean_job_text(description) or description,
        }

    @staticmethod
    def _page_listing_snapshot(page: Page) -> dict[str, Any]:
        try:
            snapshot = page.evaluate(
                """
                () => {
                  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                  const asList = (node) => {
                    if (!node) return [];
                    if (Array.isArray(node)) return node.flatMap(asList);
                    if (Array.isArray(node["@graph"])) return node["@graph"];
                    return [node];
                  };
                  let posting = null;
                  for (const script of document.querySelectorAll(
                    'script[type="application/ld+json"]'
                  )) {
                    try {
                      for (const node of asList(JSON.parse(script.textContent || "null"))) {
                        const types = [].concat(
                          node && node["@type"] ? node["@type"] : []
                        );
                        if (types.includes("JobPosting")) {
                          posting = node;
                          break;
                        }
                      }
                    } catch (error) {}
                    if (posting) break;
                  }
                  const rawLocation = posting && posting.jobLocation
                    ? (Array.isArray(posting.jobLocation)
                        ? posting.jobLocation[0]
                        : posting.jobLocation)
                    : null;
                  const address = rawLocation && rawLocation.address
                    ? rawLocation.address
                    : rawLocation;
                  const locationParts = address && typeof address === "object"
                    ? [
                        address.addressLocality,
                        address.addressRegion,
                        address.addressCountry
                      ].filter(Boolean)
                    : [typeof address === "string" ? address : ""];
                  const h1 = document.querySelector("h1");
                  let scope = h1;
                  for (let index = 0; index < 8 && scope; index += 1) {
                    scope = scope.parentElement;
                  }
                  const scoped = scope || document;
                  const companyFromLink = [...scoped.querySelectorAll(
                    'a[href*="/company/"], a[href*="/school/"]'
                  )].map((anchor) => clean(anchor.textContent)).find(
                    (text) => text && text.length >= 2 && text.length <= 80
                  ) || "";
                  const insights = [...document.querySelectorAll(
                    ".job-details-jobs-unified-top-card__job-insight, "
                    + "li.job-details-fit-level-preferences, [class*='job-insight']"
                  )].map((element) => clean(element.innerText)).filter(Boolean);
                  return {
                    documentTitle: document.title || "",
                    h1: h1 ? clean(h1.innerText) : "",
                    companyFromLink,
                    insights,
                    jsonLd: posting ? {
                      title: posting.title || "",
                      company: (posting.hiringOrganization
                        && posting.hiringOrganization.name) || "",
                      location: locationParts.filter(Boolean).join(", "),
                      employmentType: posting.employmentType || ""
                    } : null
                  };
                }
                """
            )
        except PlaywrightError:
            snapshot = {}
        return snapshot if isinstance(snapshot, dict) else {}

    @staticmethod
    def _first_visible_text(page: Page, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            locator: Locator = page.locator(selector)
            try:
                count = min(int(locator.count()), 8)
            except PlaywrightError:
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    if not item.is_visible():
                        continue
                    text = item.inner_text(timeout=5_000).strip()
                except PlaywrightError:
                    continue
                if text:
                    return text
        return ""

    @staticmethod
    def _first_text(page: Page, selectors: tuple[str, ...]) -> str:
        return LinkedInDiscovery._first_visible_text(page, selectors)

    @staticmethod
    def _title_from_document(document_title: str, company: str) -> str:
        title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", document_title).strip()
        if company:
            title = re.sub(
                rf"\s*\|\s*{re.escape(company)}\s*$",
                "",
                title,
                flags=re.IGNORECASE,
            ).strip()
        return title

    @staticmethod
    def _new_layout_description(page: Page) -> str:
        return page.evaluate(
            """
            () => {
              const names = new Set([
                "about the job", "acerca del empleo",
                "sobre el empleo", "om jobben"
              ]);
              const heading = [...document.querySelectorAll(
                "h1, h2, h3, [role='heading']"
              )].find(element =>
                names.has((element.textContent || "").trim().toLowerCase())
              );
              if (!heading) return "";
              let container = heading.parentElement;
              while (container && container !== document.body) {
                const text = (container.innerText || "").trim();
                if (text.length >= 120 && text.length <= 30000) {
                  return text.replace(
                    /^(About the job|Acerca del empleo|Sobre el empleo|Om jobben)\\s*/i,
                    ""
                  ).trim();
                }
                container = container.parentElement;
              }
              return "";
            }
            """
        )

    @staticmethod
    def _new_layout_location(page: Page, title: str) -> str:
        if not title:
            return ""
        title_element = page.get_by_text(title, exact=True).first
        if not title_element.count():
            return ""
        metadata = title_element.locator(
            "xpath=following::p[normalize-space()][1]"
        )
        if not metadata.count():
            return ""
        text = metadata.inner_text(timeout=5_000).strip()
        return text.split("·", 1)[0].strip()


def discover(
    query: str,
    location: str,
    limit: int = 20,
) -> list[JobState]:
    """Run production LinkedIn discovery with the persistent browser profile."""

    discovery = LinkedInDiscovery()
    with session(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        discovery.ensure_login(page)
        return discovery.discover_with_page(page, query, location, limit)
