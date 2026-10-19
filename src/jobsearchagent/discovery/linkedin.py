"""Authenticated LinkedIn discovery producing normalized ``JobState`` seeds."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..browser import session
from ..config import get_settings, linkedin_search_url, load_searches
from ..database import seen, upsert_job
from ..models import JobState
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
                continue
            try:
                state = self._scrape_job(page, url)
            except (PlaywrightTimeoutError, RuntimeError) as exc:
                self.output(f"Could not read {url}: {exc}")
                continue
            if self._title_is_excluded(str(state.get("title", ""))):
                continue
            upsert_job(state)
            jobs.append(state)
            if len(jobs) >= limit:
                break
        return jobs

    @staticmethod
    def build_search_url(
        query: str,
        location: str,
        *,
        start: int = 0,
    ) -> str:
        """Build a recent, remote, Easy Apply-optimized LinkedIn URL."""

        base = linkedin_search_url(query, location, easy_apply=True)
        parts = urlsplit(base)
        parameters = dict(parse_qsl(parts.query, keep_blank_values=True))
        parameters["f_WT"] = "2"
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
        try:
            page.goto(url, wait_until="commit", timeout=60_000)
            page.locator("body").wait_for(state="attached", timeout=15_000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"LinkedIn did not produce a usable document while loading: {url}"
            ) from exc

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

        company = self._first_text(
            page,
            (
                ".job-details-jobs-unified-top-card__company-name",
                ".job-details-jobs-unified-top-card__primary-description-container "
                "a[href*='/company/']",
                "a[href*='/company/']",
            ),
        )
        title = self._first_text(
            page,
            (
                ".job-details-jobs-unified-top-card__job-title h1",
                ".job-details-jobs-unified-top-card__job-title",
                "h1",
            ),
        ) or self._title_from_document(page.title(), company)
        location = self._first_text(
            page,
            (
                ".job-details-jobs-unified-top-card__primary-description-container",
                ".jobs-unified-top-card__bullet",
            ),
        ) or self._new_layout_location(page, title)
        description = self._first_text(
            page,
            (
                "#job-details",
                ".jobs-description__content",
                ".jobs-box__html-content",
            ),
        ) or self._new_layout_description(page)
        if not title or not description:
            raise RuntimeError("LinkedIn job title or description was not available")

        easy_apply, apply_url = self._application_details(page)
        canonical_url = canonicalize_url(url)
        return JobState(
            url=canonical_url,
            source_job_id=extract_linkedin_job_id(url) or "",
            title=clean_job_text(title),
            company=clean_job_text(company) or "Unknown company",
            location=clean_job_text(location) or "Unknown location",
            site=SITE,
            raw_description=clean_job_text(description),
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

    @staticmethod
    def _first_text(page: Page, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            locator: Locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                text = locator.inner_text(timeout=5_000).strip()
                if text:
                    return text
        return ""

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
