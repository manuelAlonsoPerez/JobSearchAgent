"""Part M6: EuroTechJobs keyword search and remote category."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from jobsearchagent.config import site_by_id
from jobsearchagent.discovery.eurotechjobs import EuroTechJobsAdapter
from jobsearchagent.discovery.loop import SearchUnit, search_units
from jobsearchagent.discovery.registry import adapter_for


SEARCH_HTML = """
<ul class="searchList">
  <li class="standardJobContainer">
    <h3><a href="/job_display/42/Frontend_Developer_Example_Remote">Frontend Developer</a></h3>
    <div class="companyName">Example</div>
    <div class="location">Remote, Spain</div>
    <p>Card excerpt.</p>
    <span class="badge">Remote</span>
    <div class="postedDate">Posted 2 days ago</div>
  </li>
  <li class="standardJobContainer">
    <h3><a href="/job_display/43/Onsite_Role_Berlin">Onsite Role</a></h3>
    <div class="companyName">Other</div>
    <div class="location">Berlin, Germany</div>
    <p>Office only.</p>
    <span class="badge">On-site</span>
    <div class="postedDate">Posted 1 day ago</div>
  </li>
  <li class="standardJobContainer">
    <h3><a href="/job_display/44/Old_Remote_Role">Old Remote Role</a></h3>
    <div class="companyName">Archive</div>
    <div class="location">Remote, Norway</div>
    <p>Too old.</p>
    <span class="badge">Remote</span>
    <div class="postedDate">Posted 400 days ago</div>
  </li>
</ul>
"""

DETAIL_HTML = """
<div class="jobDisplay">
  <p>Build interfaces in React for a European product team.</p>
</div>
<div class="apply">Don't forget to mention EuroTechJobs.</div>
<button data-job-url="https://boards.example.com/jobs/42">Apply Now</button>
"""

REMOTE_HTML = """
<ul class="searchList">
  <li class="standardJobContainer">
    <h3><a href="/job_display/90/Remote_Engineer">Remote Engineer</a></h3>
    <div class="companyName">Mailer</div>
    <div class="location">Remote, EMEA</div>
    <p>Keep the product running.</p>
    <div class="postedDate">Posted today</div>
  </li>
</ul>
"""


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self._body = body

    def text(self) -> str:
        return self._body


class _Page:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.urls: list[str] = []
        self.request = self

    def get(self, url: str, timeout: int | None = None) -> _Response:
        del timeout
        self.urls.append(url)
        for key, body in self.pages.items():
            if key in url:
                return _Response(200, body)
        return _Response(404, "")


class EuroTechJobsTests(unittest.TestCase):
    def test_registry_resolves_both_rows(self) -> None:
        self.assertEqual(adapter_for("eurotechjobs").id, "eurotechjobs")

    def test_service_now_phrase_becomes_a_keyword_path(self) -> None:
        site = site_by_id("eurotechjobs")
        units = search_units(
            site,
            ["ServiceNow developer", "React developer"],
            ["Norway", "Remote"],
        )
        self.assertEqual(
            [(unit.query, unit.location) for unit in units],
            [("servicenow", None), ("react", None)],
        )
        page = _Page({"/keyword/servicenow": SEARCH_HTML, "/job_display/42/": DETAIL_HTML})
        with tempfile.TemporaryDirectory() as directory:
            jobs = EuroTechJobsAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            ).discover(page, site, "servicenow", None, 5, {"work_type": "remote", "month": 1})
        self.assertTrue(page.urls[0].endswith("/job_search/keyword/servicenow"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Frontend Developer")
        self.assertEqual(
            jobs[0]["raw_description"],
            "Build interfaces in React for a European product team.",
        )
        self.assertEqual(jobs[0]["apply_url"], "https://boards.example.com/jobs/42")
        self.assertFalse(jobs[0]["easy_apply"])
        self.assertEqual(jobs[0]["source_job_id"], "42")
        self.assertNotIn("/job_display/43/", " ".join(page.urls))

    def test_remote_row_opens_its_category_once(self) -> None:
        site = site_by_id("eurotechjobs-remote")
        units = search_units(
            site,
            ["ServiceNow developer", "React developer", "Python developer"],
            ["Norway", "Spain"],
        )
        self.assertEqual(units, [SearchUnit(query=None, location=None)])
        page = _Page({"/jobs/remote": REMOTE_HTML, "/job_display/90/": "<div class=\"jobDisplay\"><p>Keep the product running.</p></div>"})
        with tempfile.TemporaryDirectory() as directory:
            jobs = EuroTechJobsAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            ).discover(page, site, "servicenow", "Norway", 5, {})
        self.assertEqual(page.urls[0], "https://www.eurotechjobs.com/jobs/remote")
        self.assertEqual(len(jobs), 1)
        self.assertNotIn("keyword", page.urls[0])

    def test_second_read_of_the_same_job_is_seen(self) -> None:
        site = site_by_id("eurotechjobs")
        pages = {"/keyword/servicenow": SEARCH_HTML, "/job_display/42/": DETAIL_HTML}
        with tempfile.TemporaryDirectory() as directory:
            adapter = EuroTechJobsAdapter(
                database_path=Path(directory) / "jobs.db",
                output_fn=lambda _line: None,
            )
            first = adapter.discover(
                _Page(pages), site, "servicenow", None, 5, {"work_type": "remote", "month": 1}
            )
            second = adapter.discover(
                _Page(pages), site, "servicenow", None, 5, {"work_type": "remote", "month": 1}
            )
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_blocked_page_returns_no_jobs(self) -> None:
        class Broken:
            request = None

            def get(self, url: str, timeout: int | None = None) -> _Response:
                raise RuntimeError("HTTP 403")

        Broken.request = Broken()
        jobs = EuroTechJobsAdapter(output_fn=lambda _line: None).discover(
            Broken(),
            site_by_id("eurotechjobs"),
            "servicenow",
            None,
            5,
            {},
        )
        self.assertEqual(jobs, [])
