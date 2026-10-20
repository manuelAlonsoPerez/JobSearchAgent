"""Tests for Phase 2 artifact-requirement analysis."""

from __future__ import annotations

import unittest
from typing import Any

from jobsearchagent.models import JobState
from jobsearchagent.triage.requirements import decide_requirements


class StubJev:
    def __init__(self, probabilities: list[float]) -> None:
        self.probabilities = iter(probabilities)
        self.calls: list[tuple[Any, str]] = []

    def noul(self, state: Any, instructions: str) -> float:
        self.calls.append((state, instructions))
        return next(self.probabilities)


class RequirementsTests(unittest.TestCase):
    def test_accepted_easy_apply_job_requires_v1_artifacts(self) -> None:
        jev = StubJev([])
        update = decide_requirements(
            JobState(
                outcome="accept",
                easy_apply=True,
                title="ServiceNow Developer",
            ),
            jev=jev,  # type: ignore[arg-type]
        )

        self.assertEqual(
            update["required_artifacts"],
            ["cv", "cover_letter"],
        )
        self.assertEqual(jev.calls, [])

    def test_non_accepted_or_external_jobs_require_no_artifacts(self) -> None:
        rejected = decide_requirements(
            JobState(outcome="reject", easy_apply=True)
        )
        external = decide_requirements(
            JobState(outcome="accept", easy_apply=False)
        )

        self.assertEqual(rejected["required_artifacts"], [])
        self.assertEqual(external["required_artifacts"], [])

    def test_future_optional_artifacts_use_independent_noul_gates(self) -> None:
        jev = StubJev([0.8, 0.2])
        update = decide_requirements(
            JobState(
                outcome="accept",
                easy_apply=True,
                title="Frontend Developer",
                raw_description="Apply with supporting materials.",
                profile_id="frontend-developer",
            ),
            jev=jev,  # type: ignore[arg-type]
            optional_artifacts={
                "portfolio": "Does this application require a portfolio?",
                "project_blurb": "Does it require a separate project description?",
            },
            noul_threshold=0.5,
        )

        self.assertEqual(
            update["required_artifacts"],
            ["cv", "cover_letter", "portfolio"],
        )
        self.assertEqual(len(jev.calls), 2)


if __name__ == "__main__":
    unittest.main()
