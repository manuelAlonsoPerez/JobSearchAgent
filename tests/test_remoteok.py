"""Part M7: RemoteOK adapter."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jobsearchagent.config import site_by_id
from jobsearchagent.discovery.registry import adapter_for
from jobsearchagent.discovery.remoteok import RemoteOKAdapter


LISTING = "https://remoteOK.com/remote-jobs/remote-frontend-developer-example-42"


class _Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _Page:
    def __init__(self, payload: Any, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.urls: list[str] = []
        self.request = self

    def get(self, url: str, timeout: int | None = None) -> _Response:
        del timeout
        self.urls.append(url)
        return _Response(self.status, self.payload)


def _job(**overrides: Any) -> dict[str, Any]:
    listing = {
        "id": 42,
        "position": "Frontend Developer",
        "company": "Example",
        "location": "EMEA",
        "description": "<p>Build interfaces in React.</p>",
        "url": LISTING,
        "apply_url": LISTING,
        "date": datetime.now(timezone.utc).isoformat(),
    }
    listing.update(overrides)
    return listing


class RemoteOKAdapterTests(unittest.TestCase):
    def test_registry_resolves_remoteok(self) -> None:
        self.assertEqual(adapter_for("remoteok").id, "remoteok")

    def test_fixture_yields_one_job_and_the_second_call_is_seen(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        payload = [
            {"legal": "API terms"},
            _job(),
            _job(id=44, position="Old Role", company="Archive", date=old),
            _job(id=45, position="After The Cut", company="Later"),
        ]
        site = site_by_id("remoteok")
        page = _Page(payload)
        with tempfile.TemporaryDirectory() as directory:
            adapter = RemoteOKAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            jobs = adapter.discover(page, site, "React developer", "Norway", 5, {"month": 1})
            again = adapter.discover(
                _Page(payload), site, None, None, 5, {"month": 1}
            )
        self.assertEqual(page.urls, ["https://remoteok.com/remote-dev-jobs.json"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Frontend Developer")
        self.assertEqual(jobs[0]["company"], "Example")
        self.assertEqual(jobs[0]["location"], "EMEA")
        self.assertEqual(jobs[0]["raw_description"], "Build interfaces in React.")
        self.assertEqual(jobs[0]["source_job_id"], "42")
        self.assertEqual(jobs[0]["site_id"], "remoteok")
        self.assertFalse(jobs[0]["easy_apply"])
        self.assertEqual(
            jobs[0]["apply_url"],
            "https://remoteok.com/remote-jobs/remote-frontend-developer-example-42",
        )
        self.assertEqual(again, [])

    def test_missing_date_is_kept(self) -> None:
        payload = [_job(date="", epoch=None)]
        with tempfile.TemporaryDirectory() as directory:
            jobs = RemoteOKAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            ).discover(
                _Page(payload),
                site_by_id("remoteok"),
                None,
                None,
                5,
                {"month": 1},
            )
        self.assertEqual(len(jobs), 1)

    def test_blocked_feed_returns_no_jobs(self) -> None:
        class Broken:
            request = None

            def get(self, url: str, timeout: int | None = None) -> _Response:
                raise RuntimeError("HTTP 403")

        Broken.request = Broken()
        jobs = RemoteOKAdapter(output_fn=lambda _line: None).discover(
            Broken(),
            site_by_id("remoteok"),
            None,
            None,
            5,
            {},
        )
        self.assertEqual(jobs, [])
