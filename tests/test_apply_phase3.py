"""Tests for Phase 3 apply submit (A7) and record (A8)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobsearchagent.apply.agent import apply_to_job
from jobsearchagent.apply.record import record
from jobsearchagent.apply.submit import submit_application
from jobsearchagent.browser import session
from jobsearchagent.database import count_applied_today, get_job, upsert_job
from jobsearchagent.models import JobState


def _review_html(submit: bool = True) -> str:
    submit_block = (
        '<button type="button" id="submit">Submit application</button>'
        if submit
        else ""
    )
    return f"""
        <main>
          <div id="modal">
            <h2>Apply to Empiric</h2>
            <h3>Review your application</h3>
            {submit_block}
          </div>
        </main>
        <script>
          window.submitClicks = 0;
          const submit = document.getElementById("submit");
          if (submit) {{
            submit.onclick = () => {{
              window.submitClicks += 1;
              document.getElementById("modal").innerHTML =
                "<h2>Apply to Empiric</h2><p>Your application was sent</p>";
            }};
          }}
        </script>
        """


class SubmitTests(unittest.TestCase):
    def test_dry_run_reaches_review_without_submit(self) -> None:
        state: JobState = {
            "url": "https://www.linkedin.com/jobs/view/123/",
            "easy_apply": True,
            "site": "LinkedIn Jobs",
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"LINKEDIN_ACTION_DELAY_MS": "0"}),
            session(
                profile_dir=Path(temporary) / "profile",
                headless=True,
            ) as context,
        ):
            page = context.pages[0] if context.pages else context.new_page()
            page.set_content(_review_html())
            submit_application(page, state, dry_run=True, output_fn=lambda _: None)

            self.assertEqual(page.evaluate("window.submitClicks"), 0)
            self.assertEqual(state.get("apply_status"), "dry_run_ready")
            self.assertTrue(state.get("messages"))

    def test_live_clicks_submit_once(self) -> None:
        state: JobState = {
            "url": "https://www.linkedin.com/jobs/view/123/",
            "easy_apply": True,
            "site": "LinkedIn Jobs",
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"LINKEDIN_ACTION_DELAY_MS": "0"}),
            session(
                profile_dir=Path(temporary) / "profile",
                headless=True,
            ) as context,
        ):
            page = context.pages[0] if context.pages else context.new_page()
            page.set_content(_review_html())
            submit_application(page, state, dry_run=False, output_fn=lambda _: None)

            self.assertEqual(page.evaluate("window.submitClicks"), 1)
            self.assertEqual(state.get("apply_status"), "applied")

    def test_external_job_is_not_automated(self) -> None:
        state: JobState = {
            "url": "https://www.linkedin.com/jobs/view/123/",
            "apply_url": "https://boards.example.com/job/9",
            "easy_apply": False,
            "site": "LinkedIn Jobs",
        }
        submit_application(None, state, dry_run=False, output_fn=lambda _: None)

        self.assertEqual(state.get("apply_status"), "external")
        self.assertEqual(state.get("apply_url"), "https://boards.example.com/job/9")

    def test_daily_limit_blocks_live_submit(self) -> None:
        state: JobState = {
            "url": "https://www.linkedin.com/jobs/view/999/",
            "easy_apply": True,
            "site": "LinkedIn Jobs",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            for index in range(2):
                upsert_job(
                    JobState(
                        site="LinkedIn Jobs",
                        source_job_id=str(index),
                        url=f"https://www.linkedin.com/jobs/view/{index}/",
                        easy_apply=True,
                        apply_status="applied",
                    ),
                    path=path,
                )
            self.assertEqual(count_applied_today("LinkedIn Jobs", path=path), 2)
            with patch(
                "jobsearchagent.apply.submit.get_settings",
            ) as settings:
                settings.return_value.linkedin_daily_apply_limit = 2
                settings.return_value.dry_run = False
                submit_application(
                    None,
                    state,
                    dry_run=False,
                    database_path=path,
                    output_fn=lambda _: None,
                )
        self.assertEqual(state.get("apply_status"), "skipped")
        self.assertIn("limit", str(state.get("review_reason")).casefold())

    def test_deferred_required_fields_are_not_submitted(self) -> None:
        state: JobState = {
            "url": "https://www.linkedin.com/jobs/view/123/",
            "easy_apply": True,
            "site": "LinkedIn Jobs",
            "apply_status": "manual",
            "review_reason": "Easy Apply needs review: favorite color",
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"LINKEDIN_ACTION_DELAY_MS": "0"}),
            session(
                profile_dir=Path(temporary) / "profile",
                headless=True,
            ) as context,
        ):
            page = context.pages[0] if context.pages else context.new_page()
            page.set_content(_review_html())
            submit_application(page, state, dry_run=False, output_fn=lambda _: None)

            self.assertEqual(page.evaluate("window.submitClicks"), 0)
            self.assertEqual(state.get("apply_status"), "manual")


class RecordTests(unittest.TestCase):
    def test_applied_job_is_not_queued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            state: JobState = {
                "site": "LinkedIn Jobs",
                "source_job_id": "1",
                "url": "https://www.linkedin.com/jobs/view/1/",
                "easy_apply": True,
                "apply_status": "applied",
            }
            result = record(state, path=path)
            stored = get_job("1", path=path)
            self.assertIsNone(result["review"])
            self.assertEqual(stored and stored.get("apply_status"), "applied")

    def test_manual_and_external_go_to_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            manual: JobState = {
                "site": "LinkedIn Jobs",
                "source_job_id": "2",
                "url": "https://www.linkedin.com/jobs/view/2/",
                "easy_apply": True,
                "apply_status": "manual",
                "review_reason": "Unknown screening question",
            }
            external: JobState = {
                "site": "LinkedIn Jobs",
                "source_job_id": "3",
                "url": "https://www.linkedin.com/jobs/view/3/",
                "apply_url": "https://boards.example.com/3",
                "easy_apply": False,
                "apply_status": "external",
            }
            manual_result = record(manual, path=path)
            external_result = record(external, path=path)

            self.assertEqual(
                manual_result["review"],
                {
                    "url": "https://www.linkedin.com/jobs/view/2/",
                    "reason": "Unknown screening question",
                    "link": "https://www.linkedin.com/jobs/view/2/",
                },
            )
            self.assertEqual(external_result["review"]["link"], "https://boards.example.com/3")
            with sqlite3.connect(path) as connection:
                rows = connection.execute(
                    "SELECT url, reason, link FROM review_queue ORDER BY id"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][2], "https://boards.example.com/3")

    def test_needs_confirmation_is_queued_even_without_apply_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            state: JobState = {
                "site": "LinkedIn Jobs",
                "source_job_id": "4",
                "url": "https://www.linkedin.com/jobs/view/4/",
                "easy_apply": True,
                "outcome": "needs_confirmation",
            }
            result = record(state, path=path)
            self.assertIsNotNone(result["review"])
            self.assertIn("confirmation", result["review"]["reason"].casefold())

    def test_dry_run_ready_is_not_queued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            state: JobState = {
                "site": "LinkedIn Jobs",
                "source_job_id": "5",
                "url": "https://www.linkedin.com/jobs/view/5/",
                "easy_apply": True,
                "apply_status": "dry_run_ready",
            }
            result = record(state, path=path)
            self.assertIsNone(result["review"])
            stored = get_job("5", path=path)
            self.assertEqual(stored and stored.get("apply_status"), "dry_run_ready")


class ApplyAgentTests(unittest.TestCase):
    def test_external_decision_is_recorded_without_a_browser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            result = apply_to_job(
                {
                    "url": "https://www.linkedin.com/jobs/view/8/",
                    "easy_apply": False,
                    "apply_url": "https://ats.example/8",
                    "profile_id": "servicenow-developer",
                    "title": "ServiceNow Developer",
                },
                database_path=path,
                dry_run=True,
                output_fn=lambda _: None,
            )
            stored = get_job("8", path=path)
        self.assertEqual(result["apply_status"], "external")
        self.assertEqual(result["review"]["link"], "https://ats.example/8")
        self.assertEqual(stored and stored.get("apply_status"), "external")
