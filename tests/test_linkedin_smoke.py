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
        self.request: dict[str, Any] | None = None

    def ask(self, state: Any, questions: Any) -> dict[str, Any]:
        self.request = {"state": state, "questions": questions}
        return {
            "profile": {"choice": "servicenow-developer", "confidence": 0.9},
            "outcome": {"choice": "accept", "confidence": 0.8},
            "fit": {"score": 1.7},
        }

    def noul(self, state: Any, instructions: str) -> float:
        return 0.9

    def close(self) -> None:
        return None


class LinkedInScraperSmokeTests(unittest.TestCase):
    def test_search_url_requests_remote_easy_apply_jobs(self) -> None:
        scraper = LinkedInScraperSmokeTest(jev=StubJev())
        query = parse_qs(urlsplit(scraper.build_search_url()).query)

        self.assertEqual(query["keywords"], ["ServiceNow"])
        self.assertEqual(query["location"], ["Spain"])
        self.assertEqual(query["f_WT"], ["2"])
        self.assertEqual(query["f_AL"], ["true"])

    def test_decision_uses_all_three_typed_questions(self) -> None:
        jev = StubJev()
        scraper = LinkedInScraperSmokeTest(jev=jev)
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
        self.assertIsNotNone(jev.request)
        self.assertEqual(
            set(jev.request["questions"]),
            {"profile", "outcome", "fit"},
        )
        self.assertIn(
            "servicenow-developer",
            jev.request["questions"]["profile"]["criteria"],
        )

    def test_user_controls_whether_next_job_is_analyzed(self) -> None:
        yes = LinkedInScraperSmokeTest(
            jev=StubJev(),
            input_fn=lambda _: "yes",
        )
        no = LinkedInScraperSmokeTest(
            jev=StubJev(),
            input_fn=lambda _: "n",
        )

        self.assertTrue(yes._continue())
        self.assertFalse(no._continue())


if __name__ == "__main__":
    unittest.main()
