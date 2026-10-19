"""Tests for the production LinkedIn discovery-and-triage slice."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from jobsearchagent.database import (
    add_to_review,
    count_applied_today,
    init_db,
    record_result,
    seen,
    upsert_job,
)
from jobsearchagent.discovery.linkedin import LinkedInDiscovery
from jobsearchagent.linkedin_agent import LinkedInAgent
from jobsearchagent.models import JobState
from jobsearchagent.triage import triage


class StubJev:
    def __init__(
        self,
        *,
        location: str = "eligible",
        outcome: str = "accept",
        fit: float = 1.5,
    ) -> None:
        self.location = location
        self.outcome = outcome
        self.fit = fit
        self.requests: list[dict[str, Any]] = []

    def ask(self, state: Any, questions: Any) -> dict[str, Any]:
        self.requests.append({"state": state, "questions": questions})
        if "profile" in questions:
            return {"profile": {"choice": "frontend", "confidence": 0.9}}
        return {
            "location_eligibility": {"choice": self.location},
            "outcome": {"choice": self.outcome},
            "fit": {"score": self.fit},
        }


PROFILE_CONFIG = {
    "match_rules": {
        "definitions": {
            "confirmed_hard_blockers": ["Core-stack mismatch"],
        },
    },
    "shared": {"candidate": {"based_in": "Oslo, Norway"}},
    "work_profiles": {
        "frontend": {
            "title": "Frontend Developer",
            "summary": "React and TypeScript developer",
            "years_relevant": 8,
            "skills": [{"name": "React"}, {"name": "TypeScript"}],
            "matching_titles": ["Frontend Developer"],
        },
    },
}


class DatabaseTests(unittest.TestCase):
    def test_deduplicates_linkedin_variants_and_records_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            state = JobState(
                site="LinkedIn Jobs",
                source_job_id="123",
                url="https://www.linkedin.com/jobs/view/example-123/?tracking=x",
                title="Frontend Developer",
                company="Example",
                location="Norway",
                raw_description="Build React applications.",
                easy_apply=True,
                apply_status="applied",
            )

            init_db(path)
            upsert_job(state, path=path)
            record_result(state, path=path)
            add_to_review(state["url"], "Check one answer", path=path)

            self.assertTrue(
                seen(
                    "LinkedIn Jobs",
                    "123",
                    "https://www.linkedin.com/jobs/view/123/",
                    path=path,
                )
            )
            self.assertEqual(count_applied_today("LinkedIn Jobs", path=path), 1)
            with sqlite3.connect(path) as connection:
                jobs = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                reviews = connection.execute(
                    "SELECT COUNT(*) FROM review_queue"
                ).fetchone()[0]
            self.assertEqual(jobs, 1)
            self.assertEqual(reviews, 1)


class DiscoveryTests(unittest.TestCase):
    def test_search_url_supports_linkedin_result_pages(self) -> None:
        query = parse_qs(
            urlsplit(
                LinkedInDiscovery.build_search_url(
                    "React",
                    "Norway",
                    start=25,
                )
            ).query
        )

        self.assertEqual(query["keywords"], ["React"])
        self.assertEqual(query["location"], ["Norway"])
        self.assertEqual(query["f_WT"], ["2"])
        self.assertEqual(query["f_AL"], ["true"])
        self.assertEqual(query["start"], ["25"])

    def test_title_filters_come_from_search_configuration(self) -> None:
        discovery = LinkedInDiscovery(
            searches={"exclude_titles": ["director", "intern"]},
            output_fn=lambda _: None,
        )

        self.assertTrue(discovery._title_is_excluded("Engineering Director"))
        self.assertFalse(discovery._title_is_excluded("Frontend Developer"))


class TriageTests(unittest.TestCase):
    def test_returns_normalized_job_state_update(self) -> None:
        jev = StubJev()
        state = JobState(
            title="Frontend Developer",
            company="Example",
            location="Remote",
            raw_description="React is core; Storybook is preferred.",
            easy_apply=True,
        )

        update = triage(
            state,
            jev=jev,
            locations=["Norway", "Remote"],
            profile_config=PROFILE_CONFIG,
        )

        self.assertEqual(update["profile_id"], "frontend")
        self.assertEqual(update["outcome"], "accept")
        self.assertEqual(update["fit_score"], 1.5)
        reject_criteria = jev.requests[1]["questions"]["outcome"]["criteria"][
            "reject"
        ]
        self.assertIn("secondary-skill gaps", reject_criteria)

    def test_unknown_location_routes_to_confirmation(self) -> None:
        update = triage(
            JobState(
                title="Frontend Developer",
                location="Remote",
                raw_description="Remote role.",
            ),
            jev=StubJev(location="unknown"),
            locations=["Norway", "Remote"],
            profile_config=PROFILE_CONFIG,
        )

        self.assertEqual(update["outcome"], "needs_confirmation")
        self.assertIn("Location", update["review_reason"])


class AgentRoutingTests(unittest.TestCase):
    def test_accept_is_ready_and_reject_is_skipped(self) -> None:
        agent = LinkedInAgent.__new__(LinkedInAgent)
        accepted = JobState(outcome="accept", easy_apply=True)
        rejected = JobState(outcome="reject", easy_apply=True)

        agent._route(accepted)
        agent._route(rejected)

        self.assertEqual(accepted["apply_status"], "dry_run_ready")
        self.assertEqual(rejected["apply_status"], "skipped")


if __name__ == "__main__":
    unittest.main()
