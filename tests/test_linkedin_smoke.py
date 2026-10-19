"""Tests for the interactive LinkedIn scraper smoke-test class."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from jobsearchagent.discovery.linkedin_smoke import (
    LinkedInScraperSmokeTest,
    ScrapedJob,
)


class StubJev:
    model = "test-model"

    def __init__(
        self,
        *,
        location_choice: str = "eligible",
        outcome_choice: str = "accept",
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.location_choice = location_choice
        self.outcome_choice = outcome_choice

    def ask(self, state: Any, questions: Any) -> dict[str, Any]:
        self.requests.append({"state": state, "questions": questions})
        if "profile" in questions:
            return {
                "profile": {
                    "choice": "servicenow-developer",
                    "confidence": 0.9,
                },
            }
        return {
            "location_eligibility": {
                "choice": self.location_choice,
                "confidence": 0.9,
            },
            "outcome": {"choice": self.outcome_choice, "confidence": 0.8},
            "fit": {"score": 1.7},
        }

    def noul(self, state: Any, instructions: str) -> float:
        return 0.9

    def close(self) -> None:
        return None


class LinkedInScraperSmokeTests(unittest.TestCase):
    def test_title_falls_back_to_browser_tab_title(self) -> None:
        title = LinkedInScraperSmokeTest._title_from_document(
            "ServiceNow Developer | Example GmbH | LinkedIn",
            "Example GmbH",
        )

        self.assertEqual(title, "ServiceNow Developer")

    def test_search_url_requests_remote_easy_apply_jobs(self) -> None:
        scraper = LinkedInScraperSmokeTest(
            jev=StubJev(),
            output_fn=lambda _: None,
        )
        norway_query = parse_qs(
            urlsplit(scraper.build_search_url("NORWAY")).query
        )
        remote_query = parse_qs(
            urlsplit(scraper.build_search_url("REMOTE")).query
        )

        self.assertEqual(scraper.locations, ["NORWAY", "REMOTE"])
        self.assertEqual(norway_query["keywords"], ["ServiceNow"])
        self.assertEqual(norway_query["location"], ["NORWAY"])
        self.assertEqual(remote_query["location"], ["REMOTE"])
        self.assertEqual(norway_query["f_WT"], ["2"])
        self.assertEqual(norway_query["f_AL"], ["true"])
        self.assertEqual(norway_query["sortBy"], ["R"])
        self.assertEqual(
            norway_query["origin"],
            ["JOB_SEARCH_PAGE_JOB_FILTER"],
        )
        self.assertNotIn("currentJobId", norway_query)
        self.assertEqual(scraper.max_jobs, 3)

    def test_decision_uses_all_three_typed_questions(self) -> None:
        jev = StubJev()
        scraper = LinkedInScraperSmokeTest(
            jev=jev,
            output_fn=lambda _: None,
        )
        job = ScrapedJob(
            url="https://www.linkedin.com/jobs/view/123/",
            source_job_id="123",
            title="ServiceNow Developer",
            company="Example",
            location="Remote",
            description="Develop ServiceNow applications using Flow Designer.",
            easy_apply=True,
        )

        answers = scraper.decide(job)

        self.assertEqual(answers["outcome"]["choice"], "accept")
        self.assertEqual(len(jev.requests), 2)
        self.assertEqual(
            set(jev.requests[0]["questions"]),
            {"profile"},
        )
        self.assertEqual(
            set(jev.requests[1]["questions"]),
            {"location_eligibility", "outcome", "fit"},
        )
        self.assertIn(
            "servicenow-developer",
            jev.requests[0]["questions"]["profile"]["criteria"],
        )
        self.assertEqual(
            jev.requests[1]["state"]["selected_profile_id"],
            "servicenow-developer",
        )
        self.assertTrue(
            jev.requests[1]["state"]["location_policy"]["authoritative"]
        )
        self.assertEqual(
            jev.requests[1]["state"]["location_policy"]["configured_locations"],
            ["NORWAY", "REMOTE"],
        )

    def test_searches_all_locations_and_deduplicates_results(self) -> None:
        scraper = LinkedInScraperSmokeTest(
            locations=["Norway", "Remote"],
            max_jobs=3,
            jev=StubJev(),
            output_fn=lambda _: None,
        )
        first = "https://www.linkedin.com/jobs/view/1/"
        duplicate = "https://www.linkedin.com/jobs/view/2/"
        last = "https://www.linkedin.com/jobs/view/3/"

        with (
            patch.object(scraper, "_navigate") as navigate,
            patch.object(scraper, "_raise_for_security_challenge"),
            patch.object(
                scraper,
                "_collect_job_urls",
                side_effect=[[first, duplicate], [duplicate, last]],
            ),
        ):
            results = scraper._search_configured_locations(object())

        self.assertEqual(results, [first, duplicate, last])
        self.assertEqual(navigate.call_count, 2)

    def test_location_policy_uses_configured_locations(self) -> None:
        jev = StubJev()
        scraper = LinkedInScraperSmokeTest(
            locations=["Sweden", "Remote"],
            jev=jev,
            output_fn=lambda _: None,
        )
        job = ScrapedJob(
            url="https://www.linkedin.com/jobs/view/123/",
            source_job_id="123",
            title="Frontend Developer",
            company="Example",
            location="Remote",
            description="Build web interfaces.",
            easy_apply=True,
        )

        scraper.decide(job)

        request = jev.requests[1]
        policy = request["state"]["location_policy"]
        self.assertEqual(
            policy["configured_locations"],
            ["Sweden", "Remote"],
        )
        self.assertNotIn("Norway", str(policy))
        self.assertNotIn(
            "Norway",
            str(request["questions"]["location_eligibility"]),
        )

    def test_user_controls_whether_next_job_is_analyzed(self) -> None:
        yes = LinkedInScraperSmokeTest(
            jev=StubJev(),
            input_fn=lambda _: "yes",
            output_fn=lambda _: None,
        )
        no = LinkedInScraperSmokeTest(
            jev=StubJev(),
            input_fn=lambda _: "n",
            output_fn=lambda _: None,
        )

        self.assertTrue(yes._continue())
        self.assertFalse(no._continue())

    def test_location_policy_routes_unknown_and_ineligible_jobs(self) -> None:
        job = ScrapedJob(
            url="https://www.linkedin.com/jobs/view/123/",
            source_job_id="123",
            title="ServiceNow Developer",
            company="Example",
            location="Remote",
            description="Develop ServiceNow applications.",
            easy_apply=True,
        )
        unknown = LinkedInScraperSmokeTest(
            jev=StubJev(location_choice="unknown"),
            output_fn=lambda _: None,
        )
        ineligible = LinkedInScraperSmokeTest(
            jev=StubJev(location_choice="ineligible"),
            output_fn=lambda _: None,
        )

        self.assertEqual(
            unknown.decide(job)["outcome"]["choice"],
            "needs_confirmation",
        )
        self.assertEqual(
            ineligible.decide(job)["outcome"]["choice"],
            "reject",
        )


if __name__ == "__main__":
    unittest.main()
