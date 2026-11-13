"""Part M3: Himalayas adapter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from jobsearchagent.config import site_by_id
from jobsearchagent.discovery.himalayas import HimalayasAdapter
from jobsearchagent.discovery.registry import adapter_for


GUID = "https://himalayas.app/companies/example/jobs/frontend-developer-1"
APPLY = "https://boards.example.com/jobs/1"


class _Response:
    def __init__(self, status: int, payload: dict[str, Any] | None = None) -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class _Page:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []
        self.request = self

    def get(self, url: str, timeout: int | None = None) -> _Response:
        del timeout
        self.urls.append(url)
        if not self.responses:
            raise RuntimeError("no fixture left")
        return self.responses.pop(0)


def _job(**overrides: Any) -> dict[str, Any]:
    listing = {
        "title": "Frontend Developer",
        "companyName": "Example",
        "description": "<p>Build interfaces in React.</p>",
        "applicationLink": APPLY,
        "guid": GUID,
        "locationRestrictions": ["Spain"],
        "parentCategories": ["Developer"],
        "categories": ["Front-End-Developer"],
        "employmentType": "Full Time",
        "pubDate": None,
    }
    listing.update(overrides)
    return listing


def _feed(*jobs: dict[str, Any], cursor: str | None = None) -> dict[str, Any]:
    return {"jobs": list(jobs), "nextCursor": cursor}


class HimalayasAdapterTests(unittest.TestCase):
    def test_registry_resolves_himalayas(self) -> None:
        self.assertEqual(adapter_for("himalayas").id, "himalayas")

    def test_fixture_yields_one_job_and_the_second_call_is_seen(self) -> None:
        payload = _feed(
            _job(),
            _job(
                title="Account Executive",
                companyName="Other",
                guid="https://himalayas.app/companies/other/jobs/ae-2",
                applicationLink="https://boards.example.com/jobs/2",
                locationRestrictions=["United States"],
                parentCategories=["Sales"],
                categories=["Sales"],
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = HimalayasAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            site = site_by_id("himalayas")
            page = _Page([_Response(200, payload), _Response(200, payload)])
            first = adapter.discover(page, site, None, None, 5, {})
            second = adapter.discover(page, site, None, None, 5, {})

        self.assertEqual(len(first), 1)
        job = first[0]
        self.assertEqual(job["title"], "Frontend Developer")
        self.assertEqual(job["company"], "Example")
        self.assertEqual(job["raw_description"], "Build interfaces in React.")
        self.assertEqual(job["apply_url"], APPLY)
        self.assertEqual(job["source_job_id"], GUID)
        self.assertEqual(job["site"], "Himalayas")
        self.assertEqual(job["location"], "Spain")
        self.assertFalse(job["easy_apply"])
        self.assertEqual(second, [])
        self.assertTrue(all("keywords=" not in url for url in page.urls))
        self.assertTrue(all(url.startswith("https://himalayas.app/jobs/api?") for url in page.urls))

    def test_norway_in_a_country_list_is_kept(self) -> None:
        payload = _feed(
            _job(
                locationRestrictions=["France", "Norway", "Germany"],
                guid="https://himalayas.app/companies/example/jobs/norway-3",
            )
        )
        job = self._only(payload)
        self.assertEqual(job["location"].split(", ", 1)[0], "Norway")

    def test_worldwide_label_is_kept(self) -> None:
        payload = _feed(
            _job(
                locationRestrictions=["Worldwide"],
                guid="https://himalayas.app/companies/example/jobs/world-4",
            )
        )
        job = self._only(payload)
        self.assertEqual(job["location"], "Worldwide")

    def test_empty_country_list_is_dropped(self) -> None:
        payload = _feed(_job(locationRestrictions=[]))
        self.assertEqual(self._collect(payload), [])

    def test_old_listing_is_dropped_and_missing_date_is_kept(self) -> None:
        payload = _feed(
            _job(pubDate=1_700_000_000, guid="https://himalayas.app/companies/example/jobs/old"),
            _job(pubDate=None, guid="https://himalayas.app/companies/example/jobs/undated"),
        )
        jobs = self._collect(payload, filters={"month": 1})
        self.assertEqual([job["source_job_id"] for job in jobs], [
            "https://himalayas.app/companies/example/jobs/undated",
        ])

    def test_blocked_feed_returns_no_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = HimalayasAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            page = _Page([_Response(403)])
            jobs = adapter.discover(page, site_by_id("himalayas"), None, None, 5, {})
        self.assertEqual(jobs, [])

    def test_unreadable_feed_returns_no_jobs(self) -> None:
        class Broken:
            request = None

            def get(self, url: str, timeout: int | None = None) -> _Response:
                raise RuntimeError("HTTP 202")

        Broken.request = Broken()
        with tempfile.TemporaryDirectory() as directory:
            adapter = HimalayasAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            jobs = adapter.discover(Broken(), site_by_id("himalayas"), None, None, 5, {})
        self.assertEqual(jobs, [])

    def _only(self, payload: dict[str, Any]) -> dict[str, Any]:
        jobs = self._collect(payload)
        self.assertEqual(len(jobs), 1)
        return jobs[0]

    def _collect(
        self,
        payload: dict[str, Any],
        *,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory() as directory:
            adapter = HimalayasAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            return adapter.discover(
                _Page([_Response(200, payload)]),
                site_by_id("himalayas"),
                None,
                None,
                5,
                filters or {},
            )
