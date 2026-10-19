"""Acceptance tests for build-guide parts F1 and F2."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from jobsearchagent.config import (
    get_settings,
    linkedin_search_url,
    load_searches,
    load_sites,
    load_work_profiles,
)
from jobsearchagent.models import Artifact, Form, FormField, JobState, merge_artifacts


class ConfigTests(unittest.TestCase):
    def test_settings_load_and_expand_paths(self) -> None:
        settings = get_settings()

        self.assertTrue(settings.dry_run)
        self.assertGreater(settings.linkedin_daily_apply_limit, 0)
        self.assertFalse(str(settings.db_path).startswith("~"))
        self.assertIsInstance(settings.chrome_profile_dir, Path)
        self.assertEqual(settings.job_locations, ["NORWAY", "REMOTE"])

    def test_job_locations_accept_json_or_csv(self) -> None:
        with patch.dict(os.environ, {"JOB_LOCATIONS": '["Norway", "Remote"]'}):
            self.assertEqual(get_settings().job_locations, ["Norway", "Remote"])
        with patch.dict(os.environ, {"JOB_LOCATIONS": "Norway,Remote"}):
            self.assertEqual(get_settings().job_locations, ["Norway", "Remote"])

    def test_yaml_configuration_loads(self) -> None:
        self.assertIn("sites", load_sites())
        self.assertIn("queries", load_searches())
        self.assertIn("work_profiles", load_work_profiles())

    def test_linkedin_search_url(self) -> None:
        url = linkedin_search_url("ServiceNow developer", "Spain")

        self.assertIn("keywords=ServiceNow+developer", url)
        self.assertIn("location=Spain", url)
        self.assertIn("sortBy=R", url)
        self.assertIn("f_WT=2", url)
        self.assertIn("origin=JOB_SEARCH_PAGE_JOB_FILTER", url)
        self.assertIn("refresh=true", url)
        self.assertIn("f_AL=true", url)
        self.assertNotIn("currentJobId", url)


class ModelTests(unittest.TestCase):
    def test_models_and_artifact_reducer_round_trip(self) -> None:
        form = Form(
            url="https://www.linkedin.com/jobs/view/123/",
            step_label="Contact info",
            fields=[
                FormField(
                    id="email",
                    label="Email",
                    type="email",
                    required=True,
                )
            ],
        )
        state: JobState = {
            "url": form.url,
            "source_job_id": "123",
            "title": "ServiceNow Developer",
            "company": "Example",
            "location": "Spain",
            "site": "LinkedIn Jobs",
            "raw_description": "Build ServiceNow applications.",
            "easy_apply": True,
            "form": form,
            "artifacts": {},
            "messages": [],
        }

        pending = Artifact(type="cv", content="Draft")
        validated = pending.model_copy(
            update={"status": "validated", "fact_check_passed": True}
        )
        state["artifacts"] = merge_artifacts(
            merge_artifacts(state["artifacts"], {"cv": pending}),
            {"cv": validated},
        )

        serialized = state["artifacts"]["cv"].model_dump()
        restored = Artifact.model_validate(serialized)
        self.assertEqual(restored, validated)
        self.assertEqual(state["form"].fields[0].type, "email")


if __name__ == "__main__":
    unittest.main()
