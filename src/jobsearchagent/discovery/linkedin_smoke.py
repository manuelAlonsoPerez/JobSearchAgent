"""Interactive LinkedIn scraper and JEV triage smoke test.

This module never submits an application. Login happens manually in the
persistent browser profile, and each scraped job is evaluated one at a time.

Run from the repository root:

    PYTHONPATH=src python -m jobsearchagent.discovery.linkedin_smoke
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from ..browser import session
from ..config import (
    get_settings,
    linkedin_search_url,
    load_work_profiles,
)
from ..jev import Jev
from ..utils.text import (
    canonicalize_url,
    clean_job_text,
    extract_linkedin_job_id,
)

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


class LinkedInScraperSmokeTest:
    """Manually authenticated LinkedIn search with interactive JEV triage."""

    def __init__(
        self,
        *,
        query: str = "ServiceNow",
        location: str = "Spain",
        max_jobs: int = 10,
        easy_apply_only: bool = True,
        jev: Jev | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")
        self.query = query
        self.location = location
        self.max_jobs = max_jobs
        self.easy_apply_only = easy_apply_only
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
                search_url = self.build_search_url()
                self.output(f"\nSearching LinkedIn: {search_url}")
                page.goto(search_url, wait_until="domcontentloaded")
                self._raise_for_security_challenge(page)

                job_urls = self._collect_job_urls(page)
                if not job_urls:
                    self.output("No matching job links were found.")
                    return

                self.output(f"Found {len(job_urls)} jobs to inspect.\n")
                for index, job_url in enumerate(job_urls, start=1):
                    try:
                        job = self._scrape_job(page, job_url)
                        answers = self.decide(job)
                        self.print_result(index, len(job_urls), job, answers)
                    except Exception as exc:
                        self.output(f"\nCould not analyze {job_url}: {exc}")

                    if index < len(job_urls) and not self._continue():
                        self.output("Stopped by user.")
                        break
        finally:
            if self._owns_jev:
                self.jev.close()

    def build_search_url(self) -> str:
        """Build a recent, remote-only LinkedIn search URL."""

        base = linkedin_search_url(
            self.query,
            self.location,
            easy_apply=self.easy_apply_only,
        )
        parts = urlsplit(base)
        parameters = dict(parse_qsl(parts.query, keep_blank_values=True))
        parameters["f_WT"] = "2"  # LinkedIn workplace type: remote
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(parameters),
                "",
            )
        )

    def decide(self, job: ScrapedJob) -> dict[str, Any]:
        """Return JEV's typed profile, outcome, and fit decisions."""

        profiles = self.profile_config.get("work_profiles", {})
        if not isinstance(profiles, Mapping) or not profiles:
            raise ValueError("No work profiles are configured")

        profile_options = {
            str(profile_id): self._profile_option(profile)
            for profile_id, profile in profiles.items()
            if isinstance(profile, Mapping)
        }
        state = {
            "job": {
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": job.description[:16_000],
                "easy_apply": job.easy_apply,
            },
            "candidate_shared": self.profile_config.get("shared", {}),
            "match_rules": self.profile_config.get("match_rules", {}),
            "profiles": profile_options,
        }
        return self.jev.ask(
            state=state,
            questions={
                "profile": {
                    "type": "choice",
                    "instructions": (
                        "Select exactly one candidate profile that best matches "
                        "this job. Never combine skills from different profiles."
                    ),
                    "criteria": profile_options,
                },
                "outcome": {
                    "type": "choice",
                    "instructions": (
                        "Decide whether this candidate should apply, following "
                        "match_rules exactly."
                    ),
                    "criteria": {
                        "accept": (
                            "All required skills and constraints match the selected "
                            "profile; applying is reasonable."
                        ),
                        "reject": (
                            "A required skill is missing, explicitly not claimed, "
                            "too old, or below a stated minimum."
                        ),
                        "needs_confirmation": (
                            "The role may fit but requires an unknown language grade, "
                            "residence, authorization, schedule, or other fact."
                        ),
                    },
                },
                "fit": {
                    "type": "score",
                    "instructions": (
                        "Score the candidate's fit using only the selected profile "
                        "and the job's required qualifications."
                    ),
                    "criteria": [
                        "Poor: hard mismatches dominate",
                        "Partial: some relevant evidence but important gaps",
                        "Strong: required qualifications are well supported",
                    ],
                },
            },
        )

    def print_result(
        self,
        index: int,
        total: int,
        job: ScrapedJob,
        answers: Mapping[str, Any],
    ) -> None:
        """Print job details and JEV's typed result."""

        self.output("\n" + "=" * 78)
        self.output(f"[{index}/{total}] {job.title}")
        self.output(f"Company: {job.company}")
        self.output(f"Location: {job.location}")
        self.output(f"Easy Apply: {'yes' if job.easy_apply else 'no'}")
        self.output(f"URL: {job.url}")
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
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "Cannot reach local Ollama. Start it before running this test."
            ) from exc

    def _ensure_manual_login(self, page: Page) -> None:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        if not self._requires_login(page):
            self.output("LinkedIn persistent profile is already logged in.")
            return

        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        self.output(
            "\nComplete LinkedIn login manually in the opened browser. "
            "The script never reads or stores your credentials."
        )
        self.input("Press Enter here after LinkedIn shows your feed: ")
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
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
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
        return list(collected.values())[: self.max_jobs]

    def _scrape_job(self, page: Page, url: str) -> ScrapedJob:
        page.goto(url, wait_until="domcontentloaded")
        self._raise_for_security_challenge(page)
        self._expand_description(page)

        title = self._first_text(
            page,
            (
                ".job-details-jobs-unified-top-card__job-title h1",
                ".job-details-jobs-unified-top-card__job-title",
                "h1",
            ),
        )
        company = self._first_text(
            page,
            (
                ".job-details-jobs-unified-top-card__company-name",
                ".job-details-jobs-unified-top-card__primary-description-container "
                "a[href*='/company/']",
                "a[href*='/company/']",
            ),
        )
        location = self._first_text(
            page,
            (
                ".job-details-jobs-unified-top-card__primary-description-container",
                ".jobs-unified-top-card__bullet",
            ),
        )
        description = self._first_text(
            page,
            (
                "#job-details",
                ".jobs-description__content",
                ".jobs-box__html-content",
            ),
        )
        if not title or not description:
            raise RuntimeError("LinkedIn job title or description was not available")

        easy_apply = page.get_by_role("button", name=_EASY_APPLY).count() > 0
        return ScrapedJob(
            url=canonicalize_url(url),
            source_job_id=extract_linkedin_job_id(url) or "",
            title=clean_job_text(title),
            company=clean_job_text(company) or "Unknown company",
            location=clean_job_text(location) or "Unknown location",
            description=clean_job_text(description),
            easy_apply=easy_apply,
        )

    @staticmethod
    def _expand_description(page: Page) -> None:
        names = re.compile(
            r"show more|see more|ver más|mostrar más|vis mer",
            re.IGNORECASE,
        )
        button = page.get_by_role("button", name=names).first
        if button.count() and button.is_visible():
            button.click()

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
    def _profile_option(profile: Mapping[str, Any]) -> str:
        skills = profile.get("skills", [])
        skill_names = [
            str(skill.get("name"))
            for skill in skills
            if isinstance(skill, Mapping) and skill.get("name")
        ]
        return json.dumps(
            {
                "title": profile.get("title"),
                "summary": profile.get("summary"),
                "years_relevant": profile.get("years_relevant"),
                "skills": skill_names,
                "matching_titles": profile.get("matching_titles", []),
                "weak_fit": profile.get("weak_fit", []),
                "not_claimed": profile.get("not_claimed", []),
            },
            ensure_ascii=False,
        )

    def _continue(self) -> bool:
        answer = self.input("\nAnalyze the next job offer? [y/N]: ")
        return answer.strip().casefold() in {"y", "yes"}


def main() -> None:
    try:
        LinkedInScraperSmokeTest().run()
    except RuntimeError as exc:
        raise SystemExit(f"Cannot run LinkedIn scraper smoke test: {exc}") from None


if __name__ == "__main__":
    main()
