"""Part M5: run walks enabled sites."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jobsearchagent.cli import discover_enabled_sites, parse_args, run_batch
from jobsearchagent.config import Site, enabled_sites, site_by_id
from jobsearchagent.discovery.loop import query_text
from jobsearchagent.models import JobState


def _site(site_id: str, **overrides: object) -> Site:
    values: dict[str, object] = {
        "id": site_id,
        "name": site_id,
        "url": f"https://example.com/{site_id}",
        "type": "static",
        "enabled": True,
        "adapter": site_id,
        "auth": "none",
        "supports_query": False,
        "supports_location": False,
        "posted_within": "post_filter",
        "work_type": "url",
        "profile": "shared",
    }
    values.update(overrides)
    return Site(**values)  # type: ignore[arg-type]


def _job(index: int, site: Site) -> JobState:
    return {
        "url": f"https://example.com/{site.id}/{index}",
        "source_job_id": f"{site.id}-{index}",
        "title": f"Role {index}",
        "company": "Example",
        "location": "Norway",
        "site": site.name,
        "site_id": site.id,
        "raw_description": "Build software.",
        "easy_apply": False,
        "apply_url": f"https://jobs.example.com/{site.id}/{index}",
    }


class _Adapter:
    def __init__(self, adapter_id: str, jobs: list[JobState] | None = None, error: str | None = None) -> None:
        self.id = adapter_id
        self.jobs = list(jobs or [])
        self.error = error
        self.calls: list[tuple[Any, ...]] = []

    def discover(self, page: Any, site: Site, query: str | None, location: str | None, limit: int, filters: dict) -> list[JobState]:
        self.calls.append((site.id, query, location, limit, dict(filters)))
        if self.error:
            raise RuntimeError(self.error)
        return list(self.jobs)


@contextmanager
def _browser(**_kwargs: Any) -> Any:
    yield type("Context", (), {"pages": [object()]})()


class MultiSiteRunTests(unittest.TestCase):
    def test_flags_accept_site_and_max_jobs(self) -> None:
        args = parse_args(
            [
                "run",
                "--site",
                "wwr-fullstack",
                "--site",
                "himalayas",
                "--skip-site",
                "eurotechjobs",
                "--max-jobs",
                "2",
                "--month",
                "1",
                "--dry-run",
            ]
        )
        self.assertEqual(args.site_ids, ["wwr-fullstack", "himalayas"])
        self.assertEqual(args.skip_site_ids, ["eurotechjobs"])
        self.assertEqual(args.max_jobs, 2)
        self.assertEqual(args.month, 1)

    def test_named_site_stops_at_max_jobs_without_linkedin(self) -> None:
        site = _site("wwr-fullstack", name="We Work Remotely Full Stack", adapter="wwr")
        adapter = _Adapter("wwr", [_job(index, site) for index in range(5)])
        opened: list[str] = []

        @contextmanager
        def session_fn(**_kwargs: Any) -> Any:
            opened.append("browser")
            yield type("Context", (), {"pages": [object()]})()

        with tempfile.TemporaryDirectory() as directory:
            result = discover_enabled_sites(
                ["React developer"],
                ["Norway"],
                limit=15,
                max_jobs=2,
                sites=[site],
                adapters={"wwr": adapter},
                session_fn=session_fn,
                output_fn=lambda _line: None,
                database_path=Path(directory) / "jobs.db",
            )
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(adapter.calls[0][3], 2)
        self.assertIsNone(adapter.calls[0][1])
        self.assertEqual(opened, ["browser"])
        self.assertTrue(all("linkedin.com" not in str(job["url"]) for job in result.jobs))

    def test_a_raising_adapter_does_not_stop_the_next_site(self) -> None:
        first = _site("alpha")
        second = _site("beta")
        broken = _Adapter("alpha", error="HTTP 403")
        healthy = _Adapter("beta", [_job(1, second)])
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            result = discover_enabled_sites(
                ["React"],
                ["Norway"],
                sites=[first, second],
                adapters={"alpha": broken, "beta": healthy},
                session_fn=_browser,
                output_fn=lines.append,
                database_path=Path(directory) / "jobs.db",
            )
        self.assertEqual([job["source_job_id"] for job in result.jobs], ["beta-1"])
        self.assertIn("alpha failed: HTTP 403", "\n".join(lines))
        self.assertEqual(len(healthy.calls), 1)

    def test_unsupported_month_skips_the_site_without_a_browser(self) -> None:
        site = _site("archive", posted_within="unsupported")
        adapter = _Adapter("archive", [_job(1, site)])
        opened: list[str] = []

        @contextmanager
        def session_fn(**_kwargs: Any) -> Any:
            opened.append("browser")
            yield type("Context", (), {"pages": [object()]})()

        lines: list[str] = []
        result = discover_enabled_sites(
            ["React"],
            ["Norway"],
            month=1,
            sites=[site],
            adapters={"archive": adapter},
            session_fn=session_fn,
            output_fn=lines.append,
        )
        self.assertEqual(result.jobs, [])
        self.assertEqual(adapter.calls, [])
        self.assertEqual(opened, [])
        self.assertIn("archive skipped", "\n".join(lines))

    def test_missing_adapters_are_logged_and_himalayas_still_runs(self) -> None:
        himalayas = site_by_id("himalayas")
        adapter = _Adapter("himalayas")
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            discover_enabled_sites(
                ["React developer"],
                ["Norway", "Remote"],
                sites=enabled_sites(),
                adapters={"himalayas": adapter},
                session_fn=_browser,
                output_fn=lines.append,
                database_path=Path(directory) / "jobs.db",
            )
        text = "\n".join(lines)
        self.assertIn("eurotechjobs skipped", text)
        self.assertIn("eurotechjobs-remote skipped", text)
        self.assertEqual(adapter.calls[0][:3], ("himalayas", None, None))

    def test_blocked_linkedin_does_not_open_a_browser(self) -> None:
        opened: list[str] = []

        @contextmanager
        def session_fn(**_kwargs: Any) -> Any:
            opened.append("browser")
            yield type("Context", (), {"pages": [object()]})()

        lines: list[str] = []
        result = discover_enabled_sites(
            ["React"],
            ["Norway"],
            sites=[site_by_id("linkedin")],
            session_fn=session_fn,
            output_fn=lines.append,
        )
        self.assertEqual(result.jobs, [])
        self.assertEqual(opened, [])
        self.assertIn("will not open linkedin.com", "\n".join(lines))

    def test_token_query_uses_the_shared_helper(self) -> None:
        self.assertEqual(query_text("ServiceNow developer", "token"), "servicenow")
        self.assertEqual(query_text("full stack developer", "token"), "fullstack")
        self.assertEqual(query_text("React developer", "phrase"), "React developer")

    def test_run_queues_a_non_linkedin_job(self) -> None:
        site = _site("wwr-fullstack", name="We Work Remotely Full Stack", adapter="wwr")
        adapter = _Adapter("wwr", [_job(1, site)])
        invoked: list[str] = []

        def invoke_fn(seed: JobState, **_kwargs: Any) -> JobState:
            invoked.append(str(seed.get("site_id")))
            finished = dict(seed)
            finished["outcome"] = "accept"
            finished["apply_status"] = "external"
            return finished  # type: ignore[return-value]

        with tempfile.TemporaryDirectory() as directory:
            counts = run_batch(
                ["React"],
                ["Norway"],
                sites=[site],
                adapters={"wwr": adapter},
                session_fn=_browser,
                invoke_fn=invoke_fn,
                skip_preflight=True,
                output_fn=lambda _line: None,
                database_path=Path(directory) / "jobs.db",
            )
        self.assertEqual(invoked, ["wwr-fullstack"])
        self.assertEqual(counts.discovered, 1)
        self.assertEqual(counts.external, 1)
