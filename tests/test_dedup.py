"""Part M4: thread ids and cross-site dedup."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobsearchagent.database import get_job, init_db, seen, upsert_job
from jobsearchagent.graph import thread_id_for
from jobsearchagent.models import JobState


def _job(**overrides: object) -> JobState:
    state: JobState = {
        "url": "https://himalayas.app/companies/example/jobs/frontend-1",
        "source_job_id": "himalayas-frontend-1",
        "title": "Frontend Developer",
        "company": "Acme",
        "location": "Oslo, Norway",
        "site": "Himalayas",
        "site_id": "himalayas",
        "raw_description": "First description.",
        "easy_apply": False,
        "apply_url": "https://boards.greenhouse.io/acme/jobs/9?utm=1",
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class DedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "jobs.db"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_saved_linkedin_thread_id_stays_stable(self) -> None:
        upsert_job(
            _job(
                url="https://www.linkedin.com/jobs/view/123/?trk=feed",
                source_job_id="123",
                site="LinkedIn Jobs",
                site_id="linkedin",
                apply_url=None,
                title="ServiceNow Developer",
            ),
            path=self.path,
        )
        loaded = get_job("123", path=self.path)
        assert loaded is not None
        self.assertEqual(loaded.get("site_id"), "linkedin")
        self.assertEqual(thread_id_for(loaded), "linkedin:123")

        columns = {
            row[1]
            for row in sqlite3.connect(self.path).execute("PRAGMA table_info(jobs)")
        }
        self.assertIn("site_id", columns)
        self.assertIn("content_fingerprint", columns)
        indexes = {
            row[0]
            for row in sqlite3.connect(self.path).execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertIn("jobs_content_fingerprint", indexes)

    def test_same_apply_url_on_two_sites_is_one_job(self) -> None:
        upsert_job(_job(), path=self.path)
        second = _job(
            url="https://www.linkedin.com/jobs/view/999/",
            source_job_id="999",
            site="LinkedIn Jobs",
            site_id="linkedin",
            title="A different title",
            company="Another company",
            location="Spain",
            raw_description="Second description.",
            apply_url="https://boards.greenhouse.io/acme/jobs/9",
        )
        self.assertTrue(
            seen(
                "LinkedIn Jobs",
                "999",
                second["url"],
                path=self.path,
                site_id="linkedin",
                apply_url=second["apply_url"],
                company=second["company"],
                title=second["title"],
                location=second["location"],
            )
        )
        upsert_job(second, path=self.path)
        self.assertEqual(self._count(), 1)
        original = get_job(
            "https://himalayas.app/companies/example/jobs/frontend-1",
            path=self.path,
        )
        assert original is not None
        self.assertEqual(original["raw_description"], "First description.")

    def test_same_company_title_and_location_is_one_job(self) -> None:
        upsert_job(
            _job(apply_url="https://jobs.example.com/one"),
            path=self.path,
        )
        second = _job(
            url="https://weworkremotely.com/remote-jobs/frontend-2",
            source_job_id="wwr-2",
            site="We Work Remotely Full Stack",
            site_id="wwr-fullstack",
            company="ACME!",
            title="Frontend Developer",
            location="oslo norway",
            apply_url="https://jobs.example.com/two",
            raw_description="Second description.",
        )
        self.assertTrue(
            seen(
                second["site"],
                second["source_job_id"],
                second["url"],
                path=self.path,
                site_id=second["site_id"],
                apply_url=second["apply_url"],
                company=second["company"],
                title=second["title"],
                location=second["location"],
            )
        )
        upsert_job(second, path=self.path)
        self.assertEqual(self._count(), 1)

    def test_unknown_company_does_not_collapse_jobs(self) -> None:
        upsert_job(
            _job(
                company="Unknown company",
                apply_url="https://jobs.example.com/one",
            ),
            path=self.path,
        )
        second = _job(
            url="https://weworkremotely.com/remote-jobs/other",
            source_job_id="wwr-other",
            site="We Work Remotely Full Stack",
            site_id="wwr-fullstack",
            company="Unknown company",
            apply_url="https://jobs.example.com/two",
        )
        self.assertFalse(
            seen(
                second["site"],
                second["source_job_id"],
                second["url"],
                path=self.path,
                site_id=second["site_id"],
                apply_url=second["apply_url"],
                company=second["company"],
                title=second["title"],
                location=second["location"],
            )
        )
        upsert_job(second, path=self.path)
        self.assertEqual(self._count(), 2)

    def test_same_job_can_still_record_a_later_outcome(self) -> None:
        upsert_job(_job(), path=self.path)
        upsert_job(_job(outcome="accept", apply_status="external"), path=self.path)
        loaded = get_job(
            "https://himalayas.app/companies/example/jobs/frontend-1",
            path=self.path,
        )
        assert loaded is not None
        self.assertEqual(loaded.get("outcome"), "accept")
        self.assertEqual(self._count(), 1)

    def _count(self) -> int:
        init_db(self.path)
        connection = sqlite3.connect(self.path)
        try:
            row = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()
        finally:
            connection.close()
        return int(row[0])
