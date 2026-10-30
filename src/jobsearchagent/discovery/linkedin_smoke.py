"""Interactive LinkedIn scraper and JEV triage smoke test.

This module never submits an application. Login happens manually in the
persistent browser profile, and each scraped job is evaluated one at a time.

Run from the repository root:

    PYTHONPATH=src python -m jobsearchagent.discovery.linkedin_smoke \
        --query ServiceNow --max-jobs 3
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..browser import session
from ..config import (
    get_settings,
    linkedin_search_url,
    load_work_profiles,
)
from ..jev import Jev
from ..models import JobState
from ..triage import triage
from ..utils.text import (
    canonicalize_url,
    clean_job_text,
    extract_linkedin_job_id,
)
from .linkedin import LinkedInDiscovery

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


_EASY_APPLY = re.compile(
    r"easy apply|solicitud sencilla|enkel søknad|candidatura simplificada",
    re.IGNORECASE,
)
_LOGIN_MARKERS = ("/login", "/checkpoint", "/challenge")


@dataclass(frozen=True)
class ScrapedJob:
    url: str
    source_job_id: str
    title: str
    company: str
    location: str
    description: str
    easy_apply: bool
    employment_type: str = "unknown"


class LinkedInScraperSmokeTest:
    """Manually authenticated LinkedIn search with interactive JEV triage."""

    def __init__(
        self,
        *,
        query: str = "ServiceNow",
        location: str | None = None,
        locations: list[str] | None = None,
        max_jobs: int = 3,
        easy_apply_only: bool = True,
        date_posted_seconds: int | None = None,
        jev: Jev | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")
        if location is not None and locations is not None:
            raise ValueError("Use either location or locations, not both")
        if locations is not None:
            selected_locations = locations
        elif location is not None:
            selected_locations = [location]
        else:
            selected_locations = get_settings().job_locations
        selected_locations = [
            selected.strip() for selected in selected_locations if selected.strip()
        ]
        if not selected_locations:
            raise ValueError("At least one job location is required")

        self.query = query
        self.locations = selected_locations
        self.location = selected_locations[0]  # compatibility for existing callers
        self.max_jobs = max_jobs
        self.easy_apply_only = easy_apply_only
        self.date_posted_seconds = date_posted_seconds
        self.jev = jev or Jev()
        self._owns_jev = jev is None
        self.input = input_fn
        self.output = output_fn
        self.profile_config = load_work_profiles()

    def run(self) -> None:
        """Open LinkedIn, scrape remote jobs, and triage them interactively."""

        try:
            self._verify_jev()
            with session(headless=False) as context:
                page = context.pages[0] if context.pages else context.new_page()
                self._ensure_manual_login(page)
                job_urls = self._search_configured_locations(page)
                if not job_urls:
                    self.output("No matching job links were found.")
                    return

                self.output(f"Found {len(job_urls)} jobs to inspect.\n")
                for index, job_url in enumerate(job_urls, start=1):
                    self.output(f"\n[{index}/{len(job_urls)}] Reading {job_url}")
                    try:
                        job = self._scrape_job(page, job_url)
                    except PlaywrightTimeoutError as exc:
                        self.output(f"LinkedIn page-reading timeout: {exc}")
                    except Exception as exc:
                        self.output(f"Could not read this offer: {exc}")
                    else:
                        self.print_scraped_job(index, len(job_urls), job)
                        self.output("\nAnalyzing the extracted offer with JEV...")
                        try:
                            answers = self.decide(job)
                        except httpx.TimeoutException:
                            timeout = get_settings().jev_timeout_seconds
                            self.output(
                                "JEV timed out after "
                                f"{timeout:g} seconds. The offer was read correctly, "
                                "but the local decision model did not finish."
                            )
                        except Exception as exc:
                            self.output(f"JEV could not analyze this offer: {exc}")
                        else:
                            self.print_decision(answers)

                    # For the moment, analyze every offer automatically without
                    # the interactive "Analyze the next job offer? [y/N]" prompt.
                    # (``_continue`` is kept for tests / future re-enabling.)
        finally:
            if self._owns_jev:
                self.jev.close()

    def build_search_url(self, location: str | None = None) -> str:
        """Build a recent, remote-only LinkedIn search URL.

        When ``date_posted_seconds`` is set it overrides LinkedIn's "date
        posted" filter (``f_TPR=r<seconds>``), e.g. ``7200`` for the last two
        hours. A value of ``0`` removes the filter entirely (any time), and
        ``None`` keeps whatever the site template already defines.
        """

        base = linkedin_search_url(
            self.query,
            location or self.location,
            easy_apply=self.easy_apply_only,
        )
        parts = urlsplit(base)
        parameters = dict(parse_qsl(parts.query, keep_blank_values=True))
        parameters["f_WT"] = "2"  # LinkedIn workplace type: remote
        if self.date_posted_seconds is not None:
            if self.date_posted_seconds > 0:
                # LinkedIn "date posted" filter: r<seconds> time window.
                parameters["f_TPR"] = f"r{self.date_posted_seconds}"
            else:
                parameters.pop("f_TPR", None)  # any time
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(parameters),
                "",
            )
        )

    def _search_configured_locations(self, page: Page) -> list[str]:
        """Search every configured location and interleave unique results."""

        batches: list[list[str]] = []
        for location in self.locations:
            search_url = self.build_search_url(location)
            self.output(f"\nSearching LinkedIn for {location}: {search_url}")
            self._navigate(page, search_url)
            self._raise_for_security_challenge(page)
            batches.append(self._collect_job_urls(page))

        selected: list[str] = []
        seen: set[str] = set()
        largest_batch = max((len(batch) for batch in batches), default=0)
        for index in range(largest_batch):
            for batch in batches:
                if index >= len(batch):
                    continue
                url = batch[index]
                if url in seen:
                    continue
                seen.add(url)
                selected.append(url)
                if len(selected) >= self.max_jobs:
                    return selected
        return selected

    def decide(self, job: ScrapedJob) -> dict[str, Any]:
        """Run the production triage policy and expose its diagnostic answers."""

        update = triage(
            JobState(
                url=job.url,
                source_job_id=job.source_job_id,
                title=job.title,
                company=job.company,
                location=job.location,
                site="LinkedIn Jobs",
                raw_description=job.description,
                easy_apply=job.easy_apply,
                employment_type=job.employment_type,
            ),
            jev=self.jev,
            locations=self.locations,
            profile_config=self.profile_config,
            progress=lambda message: self.output(f"  {message}"),
        )
        details = update["triage_details"]
        return {
            "profile": details["profile"],
            "location_eligibility": details["location_eligibility"],
            "skills_outcome": details["skills_outcome"],
            "fit": details["fit"],
            "outcome": {
                "type": "policy",
                "choice": update["outcome"],
                "location_eligibility": details["location_eligibility"].get(
                    "choice"
                ),
                "skills_choice": details["skills_outcome"].get(
                    "choice"
                ),
            },
        }

    def print_scraped_job(
        self,
        index: int,
        total: int,
        job: ScrapedJob,
    ) -> None:
        """Prove that the offer was extracted before invoking JEV."""

        self.output("\n" + "=" * 78)
        self.output(f"[{index}/{total}] {job.title}")
        self.output(f"Company: {job.company}")
        self.output(f"Location: {job.location}")
        self.output(f"Employment: {job.employment_type}")
        self.output(f"Easy Apply: {'yes' if job.easy_apply else 'no'}")
        self.output(f"URL: {job.url}")
        self.output(f"Description extracted: {len(job.description)} characters")
        preview = job.description[:500]
        if len(job.description) > len(preview):
            preview += "..."
        self.output(f"\nDescription preview:\n{preview}")

    def print_decision(self, answers: Mapping[str, Any]) -> None:
        """Print JEV's typed result without claiming hidden reasoning."""

        self.output("\nJEV typed decision result (labels/probabilities, not prose reasoning):")
        self.output(json.dumps(dict(answers), indent=2, ensure_ascii=False))

    def _verify_jev(self) -> None:
        try:
            self.jev.noul(
                "The configured decision service is being checked.",
                "Is this a service health-check statement?",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RuntimeError(
                    f"JEV model {self.jev.model!r} is unavailable. "
                    f"Run: ollama pull {self.jev.model}"
                ) from exc
            raise RuntimeError(f"JEV health check failed: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "Ollama's JEV request timed out. The model may still be loading "
                "or may be too slow on the available hardware. Increase "
                "JEV_TIMEOUT_SECONDS if needed."
            ) from exc
        except httpx.ConnectError as exc:
            raise RuntimeError(
                "Cannot reach local Ollama. Start it before running this test."
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"JEV request failed: {exc}") from exc

    def _ensure_manual_login(self, page: Page) -> None:
        self._navigate(page, "https://www.linkedin.com/feed/")
        if not self._requires_login(page):
            self.output("LinkedIn persistent profile is already logged in.")
            return

        self._navigate(page, "https://www.linkedin.com/login")
        self.output(
            "\nComplete LinkedIn login manually in the opened browser. "
            "The script never reads or stores your credentials."
        )
        self.input("Press Enter here after LinkedIn shows your feed: ")
        self._navigate(page, "https://www.linkedin.com/feed/")
        if self._requires_login(page):
            raise RuntimeError(
                "LinkedIn login was not completed in the persistent profile."
            )

    @staticmethod
    def _requires_login(page: Page) -> bool:
        url = page.url.casefold()
        if any(marker in url for marker in _LOGIN_MARKERS):
            return True
        return page.locator('a[href*="/login"], input[name="session_key"]').count() > 0

    @staticmethod
    def _raise_for_security_challenge(page: Page) -> None:
        url = page.url.casefold()
        if "/checkpoint" in url or "/challenge" in url:
            raise RuntimeError(
                "LinkedIn presented a security challenge; complete it manually "
                "before retrying."
            )

    def _collect_job_urls(self, page: Page) -> list[str]:
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
                    if len(collected) >= self.max_jobs:
                        return list(collected.values())

            results = page.locator(".jobs-search-results-list").last
            if results.count():
                results.evaluate("element => element.scrollTop = element.scrollHeight")
            else:
                page.evaluate(
                    """
                    () => {
                      const root = document.scrollingElement
                        ?? document.documentElement
                        ?? document.body;
                      if (root) {
                        window.scrollTo(0, root.scrollHeight);
                      }
                    }
                    """
                )
            page.wait_for_timeout(800)
        return list(collected.values())[: self.max_jobs]

    def _scrape_job(self, page: Page, url: str) -> ScrapedJob:
        self._navigate(page, url)
        self._raise_for_security_challenge(page)
        try:
            page.locator(
                "#job-details, .jobs-description__content, "
                "[data-testid='expandable-text-box']"
            ).first.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeoutError:
            # Extraction below reports a useful error if LinkedIn never renders
            # the detail panel.
            pass
        self._expand_description(page)

        fields = LinkedInDiscovery._listing_fields(page)
        title = fields["title"]
        description = fields["description"]
        if not title or not description:
            raise RuntimeError("LinkedIn job title or description was not available")

        easy_apply = page.get_by_role("button", name=_EASY_APPLY).count() > 0
        return ScrapedJob(
            url=canonicalize_url(url),
            source_job_id=extract_linkedin_job_id(url) or "",
            title=title,
            company=fields["company"],
            location=fields["location"],
            description=clean_job_text(description) or description,
            easy_apply=easy_apply,
            employment_type=fields["employment_type"],
        )

    @staticmethod
    def _navigate(page: Page, url: str) -> None:
        """Use the production LinkedIn navigation and retry policy."""

        LinkedInDiscovery._navigate(page, url)

    @staticmethod
    def _expand_description(page: Page) -> None:
        names = re.compile(
            r"show more|see more|ver más|mostrar más|vis mer|^\s*(?:…|\.\.\.)\s*more\s*$",
            re.IGNORECASE,
        )
        controls = (
            page.get_by_role("button", name=names),
            page.get_by_text(names),
        )
        for matches in controls:
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
        """Extract the job title from LinkedIn's stable browser-tab title."""

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
        """Read the description from LinkedIn's class-name-free detail layout."""

        return page.evaluate(
            """
            () => {
              const names = new Set([
                "about the job",
                "acerca del empleo",
                "sobre el empleo",
                "om jobben"
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

    def _continue(self) -> bool:
        answer = self.input("\nAnalyze the next job offer? [y/N]: ")
        return answer.strip().casefold() in {"y", "yes"}


def _parse_date_posted(value: str) -> int:
    """Parse a ``--date-posted`` value into a LinkedIn time window in seconds.

    Accepts friendly presets (``any``, ``hour``, ``2h``, ``24h``, ``day``,
    ``week``, ``month``), compact forms like ``30m``/``2h``/``3d``/``1w``, or a
    raw number of seconds. ``any``/``all`` return ``0`` (no time filter).
    """

    normalized = value.strip().lower()
    presets = {
        "any": 0,
        "all": 0,
        "hour": 3_600,
        "day": 86_400,
        "week": 604_800,
        "month": 2_592_000,
    }
    if normalized in presets:
        return presets[normalized]

    match = re.fullmatch(r"(\d+)\s*([smhdw])", normalized)
    if match:
        amount = int(match.group(1))
        unit_seconds = {"s": 1, "m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
        return amount * unit_seconds[match.group(2)]

    if normalized.isdigit():
        return int(normalized)

    raise argparse.ArgumentTypeError(
        "Use a preset (any, hour, 2h, 24h, day, week, month), a compact form "
        "like 30m/2h/3d/1w, or a number of seconds."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively test LinkedIn discovery and JEV triage. "
            "No applications are submitted."
        )
    )
    parser.add_argument("--query", default="ServiceNow")
    parser.add_argument(
        "--location",
        action="append",
        dest="locations",
        help=(
            "Override JOB_LOCATIONS for this run. Repeat the option to search "
            "multiple locations."
        ),
    )
    parser.add_argument("--max-jobs", type=int, default=3)
    parser.add_argument(
        "--date-posted",
        type=_parse_date_posted,
        default=None,
        metavar="WINDOW",
        help=(
            "Limit results by how recently they were posted. Presets: any, "
            "hour, 2h, 24h, day, week, month; compact forms like 30m/2h/3d/1w; "
            "or raw seconds (e.g. 7200 = last 2 hours). Defaults to the site "
            "template (past week)."
        ),
    )
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="Include jobs without LinkedIn Easy Apply.",
    )
    arguments = parser.parse_args()

    try:
        LinkedInScraperSmokeTest(
            query=arguments.query,
            locations=arguments.locations,
            max_jobs=arguments.max_jobs,
            easy_apply_only=not arguments.include_external,
            date_posted_seconds=arguments.date_posted,
        ).run()
    except RuntimeError as exc:
        raise SystemExit(f"Cannot run LinkedIn scraper smoke test: {exc}") from None


if __name__ == "__main__":
    main()
