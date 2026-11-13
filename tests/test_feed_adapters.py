"""Public-feed adapters: We Work Remotely, Remotive, Working Nomads, Arbeitnow."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from jobsearchagent.config import site_by_id
from jobsearchagent.discovery.arbeitnow import ArbeitnowAdapter
from jobsearchagent.discovery.registry import adapter_for
from jobsearchagent.discovery.remotive import RemotiveAdapter
from jobsearchagent.discovery.working_nomads import WorkingNomadsAdapter
from jobsearchagent.discovery.wwr import WeWorkRemotelyAdapter


NOW = datetime.now(timezone.utc)
OLD = NOW - timedelta(days=400)


class _Response:
    def __init__(self, status: int, payload: Any, *, as_json: bool) -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self._payload = payload
        self._as_json = as_json

    def json(self) -> Any:
        if not self._as_json:
            raise ValueError("not json")
        return self._payload

    def text(self) -> str:
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload)


class _Page:
    def __init__(self, payload: Any, *, status: int = 200, as_json: bool = True) -> None:
        self.payloads = payload if isinstance(payload, dict) and all(
            isinstance(key, str) and key.startswith("http") for key in payload
        ) else None
        self.payload = None if self.payloads else payload
        self.status = status
        self.as_json = as_json
        self.urls: list[str] = []
        self.request = self

    def get(self, url: str, timeout: int | None = None) -> _Response:
        del timeout
        self.urls.append(url)
        body = self.payloads[url] if self.payloads is not None else self.payload
        return _Response(self.status, body, as_json=self.as_json)


def _wwr_item(*, title: str, link: str, when: datetime) -> str:
    return (
        "<item>"
        f"<title>{title}</title>"
        "<region>Anywhere in the World</region>"
        "<type>Full-Time</type>"
        "<description>&lt;p&gt;Build interfaces in React.&lt;/p&gt;</description>"
        f"<pubDate>{format_datetime(when)}</pubDate>"
        f"<link>{link}</link>"
        f"<guid>{link}</guid>"
        "</item>"
    )


class WeWorkRemotelyTests(unittest.TestCase):
    def test_three_rows_share_one_adapter(self) -> None:
        adapter = adapter_for("wwr")
        self.assertEqual(adapter.id, "wwr")
        self.assertIs(adapter, adapter_for("wwr", site_id="wwr-frontend"))

    def test_fixture_yields_one_job_and_the_second_call_is_seen(self) -> None:
        fresh = "https://weworkremotely.com/remote-jobs/example-frontend-developer"
        xml = (
            "<rss><channel>"
            + _wwr_item(title="Example: Frontend Developer", link=fresh, when=NOW)
            + _wwr_item(
                title="Archive: Old Role",
                link="https://weworkremotely.com/remote-jobs/old-role",
                when=OLD,
            )
            + _wwr_item(
                title="Later: Should Not Appear",
                link="https://weworkremotely.com/remote-jobs/later",
                when=NOW,
            )
            + "</channel></rss>"
        )
        site = site_by_id("wwr-fullstack")
        page = _Page(xml, as_json=False)
        with tempfile.TemporaryDirectory() as directory:
            adapter = WeWorkRemotelyAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            jobs = adapter.discover(page, site, "React developer", "Norway", 5, {"month": 1})
            again = adapter.discover(_Page(xml, as_json=False), site, None, None, 5, {"month": 1})
        self.assertEqual(
            page.urls,
            ["https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss"],
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Frontend Developer")
        self.assertEqual(jobs[0]["company"], "Example")
        self.assertEqual(jobs[0]["location"], "Anywhere in the World")
        self.assertEqual(jobs[0]["raw_description"], "Build interfaces in React.")
        self.assertEqual(jobs[0]["source_job_id"], "example-frontend-developer")
        self.assertEqual(jobs[0]["site_id"], "wwr-fullstack")
        self.assertFalse(jobs[0]["easy_apply"])
        self.assertEqual(again, [])

    def test_blocked_feed_returns_no_jobs(self) -> None:
        jobs = WeWorkRemotelyAdapter(output_fn=lambda _line: None).discover(
            _Page("", status=403, as_json=False),
            site_by_id("wwr-backend"),
            None,
            None,
            5,
            {},
        )
        self.assertEqual(jobs, [])


class RemotiveTests(unittest.TestCase):
    def test_keeps_software_and_drops_other_categories(self) -> None:
        payload = {
            "jobs": [
                _remotive(job_id=1, title="Support Agent", category="Customer Service"),
                _remotive(job_id=2, title="Frontend Developer", category="Software Development"),
                _remotive(
                    job_id=3,
                    title="Old Engineer",
                    category="Software Development",
                    when=OLD,
                    url="https://remotive.com/remote-jobs/old-engineer",
                ),
            ]
        }
        page = _Page(payload)
        with tempfile.TemporaryDirectory() as directory:
            adapter = RemotiveAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            site = site_by_id("remotive-software")
            jobs = adapter.discover(page, site, None, None, 5, {"month": 1})
            again = adapter.discover(_Page(payload), site, None, None, 5, {"month": 1})
        self.assertEqual(page.urls, ["https://remotive.com/api/remote-jobs?category=software-dev"])
        self.assertEqual([job["title"] for job in jobs], ["Frontend Developer"])
        self.assertEqual(jobs[0]["company"], "Example")
        self.assertEqual(jobs[0]["location"], "Worldwide")
        self.assertEqual(jobs[0]["source_job_id"], "2")
        self.assertEqual(jobs[0]["site_id"], "remotive-software")
        self.assertFalse(jobs[0]["easy_apply"])
        self.assertEqual(again, [])


class WorkingNomadsTests(unittest.TestCase):
    def test_keeps_development_jobs(self) -> None:
        payload = [
            _nomad(title="Sales Lead", category="Sales", url="https://www.workingnomads.com/job/go/1/"),
            _nomad(
                title="Frontend Developer",
                category="Development",
                url="https://www.workingnomads.com/job/go/2016531/",
            ),
        ]
        page = _Page(payload)
        with tempfile.TemporaryDirectory() as directory:
            adapter = WorkingNomadsAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            site = site_by_id("working-nomads")
            jobs = adapter.discover(page, site, None, None, 5, {})
            again = adapter.discover(_Page(payload), site, None, None, 5, {})
        self.assertEqual(page.urls, ["https://www.workingnomads.com/api/exposed_jobs"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Frontend Developer")
        self.assertEqual(jobs[0]["source_job_id"], "2016531")
        self.assertEqual(jobs[0]["site_id"], "working-nomads")
        self.assertEqual(again, [])


class ArbeitnowTests(unittest.TestCase):
    def test_keeps_remote_jobs_and_stops_when_the_feed_gets_old(self) -> None:
        page_one = {
            "data": [
                _arbeitnow(slug="office-role", remote=False),
                _arbeitnow(slug="remote-frontend", remote=True),
                _arbeitnow(slug="remote-old", remote=True, when=OLD),
            ],
            "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"},
        }
        page_two = {"data": [_arbeitnow(slug="page-two", remote=True)], "links": {"next": None}}
        page = _Page(
            {
                "https://www.arbeitnow.com/api/job-board-api": page_one,
                "https://www.arbeitnow.com/api/job-board-api?page=2": page_two,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = ArbeitnowAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            site = site_by_id("arbeitnow-remote")
            jobs = adapter.discover(page, site, None, None, 5, {"month": 1})
            again = adapter.discover(
                _Page(
                    {
                        "https://www.arbeitnow.com/api/job-board-api": page_one,
                        "https://www.arbeitnow.com/api/job-board-api?page=2": page_two,
                    }
                ),
                site,
                None,
                None,
                5,
                {"month": 1},
            )
        self.assertEqual(page.urls, ["https://www.arbeitnow.com/api/job-board-api"])
        self.assertEqual([job["title"] for job in jobs], ["Frontend Developer"])
        self.assertEqual(jobs[0]["source_job_id"], "remote-frontend")
        self.assertEqual(jobs[0]["location"], "Germany")
        self.assertEqual(jobs[0]["site_id"], "arbeitnow-remote")
        self.assertFalse(jobs[0]["easy_apply"])
        self.assertEqual(again, [])

    def test_blocked_feed_returns_no_jobs(self) -> None:
        jobs = ArbeitnowAdapter(output_fn=lambda _line: None).discover(
            _Page({}, status=500),
            site_by_id("arbeitnow-remote"),
            None,
            None,
            5,
            {},
        )
        self.assertEqual(jobs, [])


def _remotive(
    *,
    job_id: int,
    title: str,
    category: str,
    when: datetime = NOW,
    url: str = "https://remotive.com/remote-jobs/frontend-developer",
) -> dict[str, Any]:
    return {
        "id": job_id,
        "title": title,
        "company_name": "Example",
        "category": category,
        "candidate_required_location": "Worldwide",
        "job_type": "full_time",
        "url": url,
        "description": "<p>Build interfaces in React.</p>",
        "publication_date": when.replace(tzinfo=None).isoformat(timespec="seconds"),
    }


def _nomad(*, title: str, category: str, url: str) -> dict[str, Any]:
    return {
        "title": title,
        "company_name": "Example",
        "category_name": category,
        "url": url,
        "description": "<p>Build interfaces in React.</p>",
        "location": "Europe",
        "pub_date": NOW.isoformat(),
    }


def _arbeitnow(*, slug: str, remote: bool, when: datetime = NOW) -> dict[str, Any]:
    return {
        "slug": slug,
        "title": "Frontend Developer" if remote else "Office Role",
        "company_name": "Example",
        "description": "<p>Build interfaces in React.</p>",
        "url": f"https://www.arbeitnow.com/jobs/{slug}",
        "remote": remote,
        "location": "Germany",
        "created_at": when.timestamp(),
        "job_types": ["full-time"],
    }
