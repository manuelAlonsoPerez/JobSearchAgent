"""Local review UI API: job listing, review queue, and HTTP handlers."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jobsearchagent.database import (
    add_to_review,
    get_job_detail,
    list_jobs,
    list_review_queue,
    set_review_status,
    summarize_jobs,
    upsert_job,
)
from jobsearchagent.models import JobState
from jobsearchagent.web import create_server


def _job(**overrides: object) -> JobState:
    state: JobState = {
        "url": "https://www.linkedin.com/jobs/view/111/",
        "source_job_id": "111",
        "title": "ServiceNow Developer",
        "company": "Example",
        "location": "Spain",
        "site": "LinkedIn Jobs",
        "raw_description": "Build ServiceNow applications.",
        "easy_apply": True,
        "outcome": "accept",
        "fit_score": 1.4,
        "apply_status": "manual",
        "review_reason": "Easy Apply filled and closed without Submit; saved for manual apply.",
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class DatabaseUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "jobs.db"
        upsert_job(_job(), path=self.path)
        upsert_job(
            _job(
                url="https://www.linkedin.com/jobs/view/222/",
                source_job_id="222",
                title="Rejected role",
                outcome="reject",
                apply_status="skipped",
                review_reason=None,
            ),
            path=self.path,
        )
        add_to_review(
            "https://www.linkedin.com/jobs/view/111/",
            "Easy Apply filled and closed without Submit; saved for manual apply.",
            "https://www.linkedin.com/jobs/view/111/",
            path=self.path,
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_list_jobs_filters_and_omits_description(self) -> None:
        accepted = list_jobs(path=self.path, outcome="accept")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["title"], "ServiceNow Developer")
        self.assertNotIn("raw_description", accepted[0])
        self.assertTrue(accepted[0]["easy_apply"])

        searched = list_jobs(path=self.path, query="rejected")
        self.assertEqual(len(searched), 1)
        self.assertEqual(searched[0]["source_job_id"], "222")

        skipped = list_jobs(path=self.path, apply_status="skipped")
        self.assertEqual(len(skipped), 1)

    def test_job_detail_includes_description(self) -> None:
        detail = get_job_detail("111", path=self.path)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["raw_description"], "Build ServiceNow applications.")
        by_key = get_job_detail(detail["dedup_key"], path=self.path)
        self.assertEqual(by_key["source_job_id"], "111")

    def test_review_queue_joins_job_and_can_be_marked_done(self) -> None:
        pending = list_review_queue(path=self.path, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["title"], "ServiceNow Developer")
        self.assertEqual(pending[0]["company"], "Example")
        self.assertIn("saved for manual apply", pending[0]["reason"])

        updated = set_review_status(pending[0]["id"], "done", path=self.path)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated["status"], "done")
        self.assertEqual(list_review_queue(path=self.path, status="pending"), [])
        self.assertEqual(len(list_review_queue(path=self.path, status="done")), 1)

        stats = summarize_jobs(path=self.path)
        self.assertEqual(stats["jobs"], 2)
        self.assertEqual(stats["outcomes"]["accept"], 1)
        self.assertEqual(stats["review_done"], 1)
        self.assertEqual(stats["review_pending"], 0)


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "jobs.db"
        upsert_job(_job(), path=self.path)
        add_to_review(
            "https://www.linkedin.com/jobs/view/111/",
            "Easy Apply filled and closed without Submit; saved for manual apply.",
            "https://www.linkedin.com/jobs/view/111/",
            path=self.path,
        )
        self.server = create_server("127.0.0.1", 0, self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = int(self.server.server_address[1])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str) -> dict[str, object]:
        with urlopen(self._url(path), timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_lists_jobs_and_review_queue(self) -> None:
        payload = self._get("/api/jobs")
        self.assertEqual(payload["total"], 1)
        jobs = payload["jobs"]
        assert isinstance(jobs, list)
        self.assertEqual(jobs[0]["title"], "ServiceNow Developer")

        queue = self._get("/api/review-queue?status=pending")
        self.assertEqual(queue["total"], 1)
        items = queue["items"]
        assert isinstance(items, list)
        item_id = items[0]["id"]

        request = Request(
            self._url(f"/api/review-queue/{item_id}"),
            data=json.dumps({"status": "done"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        with urlopen(request, timeout=5) as response:
            updated = json.loads(response.read().decode("utf-8"))
        self.assertEqual(updated["status"], "done")
        self.assertEqual(self._get("/api/review-queue?status=pending")["total"], 0)

        stats = self._get("/api/stats")
        self.assertEqual(stats["review_done"], 1)

        missing = Request(
            self._url("/api/jobs/does-not-exist"),
            method="GET",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(missing, timeout=5)
        self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
