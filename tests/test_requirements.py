"""Tests for Phase 2 artifact-requirement analysis."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from jobsearchagent.database import upsert_job
from jobsearchagent.models import Form, FormField, JobState
from jobsearchagent.triage.easy_apply import detect_artifacts
from jobsearchagent.triage.requirements import decide_requirements
from jobsearchagent.triage.smoke_requirements import evaluate_persisted_job


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

    def test_smoke_runner_loads_previous_state_by_url_or_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            state = JobState(
                site="LinkedIn Jobs",
                source_job_id="4471047498",
                url="https://www.linkedin.com/jobs/view/4471047498/",
                title="Software Developer",
                company="Example",
                outcome="reject",
                fit_score=1.08,
                profile_id="fullstack-developer",
                easy_apply=True,
            )
            upsert_job(state, path=path)

            rejected = evaluate_persisted_job(state["url"], database_path=path)
            forced = evaluate_persisted_job(
                "4471047498",
                database_path=path,
                force_accepted_easy_apply=True,
            )

        self.assertEqual(rejected["required_artifacts"], [])
        self.assertEqual(rejected["previous_stage"]["outcome"], "reject")
        self.assertEqual(
            forced["required_artifacts"],
            ["cv", "cover_letter"],
        )
        self.assertTrue(forced["smoke_override_applied"])


class BrowserDetectionTests(unittest.TestCase):
    def test_resume_only_easy_apply_requires_just_a_cv(self) -> None:
        forms = [
            Form(
                url="https://www.linkedin.com/jobs/view/1/",
                step_label="Contact info",
                fields=[
                    FormField(id="email", label="Email address", type="email"),
                    FormField(id="phone", label="Mobile phone number", type="text"),
                ],
            ),
            Form(
                url="https://www.linkedin.com/jobs/view/1/",
                step_label="Resume",
                fields=[
                    FormField(id="resume", label="Resume", type="file"),
                ],
            ),
        ]

        detection = detect_artifacts(forms, ["Contact info", "Resume* Upload resume"])

        self.assertEqual(detection["required_artifacts"], ["cv"])
        self.assertTrue(detection["signals"]["cv_field_or_keyword"])
        self.assertFalse(detection["signals"]["cover_letter_field_or_keyword"])

    def test_cover_letter_field_adds_cover_letter_artifact(self) -> None:
        forms = [
            Form(
                url="https://www.linkedin.com/jobs/view/2/",
                step_label="Resume",
                fields=[
                    FormField(id="resume", label="Resume", type="file"),
                    FormField(
                        id="cover",
                        label="Cover letter",
                        type="textarea",
                    ),
                ],
            ),
        ]

        detection = detect_artifacts(forms, ["Resume", "Cover letter"])

        self.assertEqual(
            detection["required_artifacts"],
            ["cv", "cover_letter"],
        )


if __name__ == "__main__":
    unittest.main()
