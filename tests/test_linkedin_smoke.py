"""Tests for the interactive LinkedIn scraper smoke-test class."""

from __future__ import annotations

import unittest
from typing import Any
from urllib.parse import parse_qs, urlsplit

from jobsearchagent.discovery.linkedin_smoke import (
    LinkedInScraperSmokeTest,
    ScrapedJob,
)


class StubJev:
    model = "test-model"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

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
            "outcome": {"choice": "accept", "confidence": 0.8},
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
        query = parse_qs(urlsplit(scraper.build_search_url()).query)

        self.assertEqual(query["keywords"], ["ServiceNow"])
        self.assertEqual(query["location"], ["Spain"])
        self.assertEqual(query["f_WT"], ["2"])
        self.assertEqual(query["f_AL"], ["true"])
        self.assertEqual(query["sortBy"], ["R"])
        self.assertEqual(query["origin"], ["JOB_SEARCH_PAGE_JOB_FILTER"])
        self.assertNotIn("currentJobId", query)
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
            {"outcome", "fit"},
        )
        self.assertIn(
            "servicenow-developer",
            jev.requests[0]["questions"]["profile"]["criteria"],
        )
        self.assertEqual(
            jev.requests[1]["state"]["selected_profile_id"],
            "servicenow-developer",
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


if __name__ == "__main__":
    unittest.main()
