"""Pass-through writers and validator used by the G1 graph."""

from __future__ import annotations

import unittest

from jobsearchagent.models import Artifact, JobState
from jobsearchagent.preparation import validate, write_cover_letter, write_cv


class PreparationTests(unittest.TestCase):
    def test_write_cv_resolves_servicenow_source_pdf(self) -> None:
        artifact = write_cv(
            JobState(profile_id="servicenow-developer", artifacts={})
        )
        self.assertEqual(artifact.type, "cv")
        self.assertTrue(artifact.path)
        self.assertTrue(str(artifact.path).endswith(".pdf"))

    def test_validate_fails_missing_cv_and_invented_cover_letter(self) -> None:
        missing = validate(Artifact(type="cv", status="pending"), JobState())
        planted = validate(
            Artifact(
                type="cover_letter",
                content="I am a ServiceNow architect leading ITOM Discovery.",
                status="pending",
            ),
            JobState(profile_id="servicenow-developer"),
        )
        empty = validate(
            Artifact(type="cover_letter", status="pending"),
            JobState(profile_id="servicenow-developer"),
        )
        self.assertEqual(missing.status, "failed")
        self.assertEqual(planted.status, "failed")
        self.assertEqual(empty.status, "validated")
        self.assertIsNone(write_cover_letter(JobState()).content)
