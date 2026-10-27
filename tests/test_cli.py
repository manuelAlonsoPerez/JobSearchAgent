"""G2: CLI batch runner counts, flags, and discover→graph walk."""

from __future__ import annotations

import unittest
from typing import Any

from jobsearchagent.cli import (
    DiscoverResult,
    _search_places,
    classify_result,
    format_counts,
    main,
    parse_args,
    run_batch,
)
from jobsearchagent.discovery.linkedin import WORK_TYPE_CODES, SearchFilters
from jobsearchagent.models import JobState


def _job(**overrides: object) -> JobState:
    state: JobState = {
        "url": "https://www.linkedin.com/jobs/view/1/",
        "source_job_id": "1",
        "title": "ServiceNow Developer",
        "company": "Example",
        "location": "Spain",
        "site": "LinkedIn Jobs",
        "raw_description": "Build ServiceNow applications.",
        "easy_apply": True,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class ParseArgsTests(unittest.TestCase):
    def test_run_done_when_flags(self) -> None:
        args = parse_args(
            [
                "run",
                "--query",
                "ServiceNow developer",
                "--location",
                "Spain",
                "--dry-run",
            ]
        )
        self.assertEqual(args.command, "run")
        self.assertEqual(args.query, "ServiceNow developer")
        self.assertEqual(args.locations, ["Spain"])
        self.assertTrue(args.dry_run)
        self.assertTrue(args.easy_apply_only)
        self.assertEqual(args.limit, 20)

    def test_run_accepts_geo_id_and_work_type(self) -> None:
        args = parse_args(
            [
                "run",
                "--query",
                "ServiceNow developer",
                "--geo-id",
                "91000007",
                "--work-type",
                "remote",
                "--dry-run",
            ]
        )
        self.assertEqual(args.geo_id, "91000007")
        self.assertEqual(args.work_type, "remote")
        self.assertTrue(args.dry_run)

    def test_include_external_and_limit(self) -> None:
        args = parse_args(
            ["discover", "--include-external", "--limit", "3", "--query", "React"]
        )
        self.assertEqual(args.command, "discover")
        self.assertFalse(args.easy_apply_only)
        self.assertEqual(args.limit, 3)
        self.assertEqual(args.query, "React")


class ClassifyTests(unittest.TestCase):
    def test_buckets(self) -> None:
        self.assertEqual(classify_result(_job(apply_status="applied")), "applied")
        self.assertEqual(
            classify_result(_job(apply_status="dry_run_ready")),
            "dry_run_ready",
        )
        self.assertEqual(classify_result(_job(apply_status="external")), "external")
        self.assertEqual(
            classify_result(_job(outcome="reject", apply_status="skipped")),
            "rejected",
        )
        self.assertEqual(
            classify_result(_job(outcome="needs_confirmation", apply_status="manual")),
            "review",
        )
        self.assertEqual(classify_result(_job(apply_status="failed")), "review")


class RunBatchTests(unittest.TestCase):
    def test_run_walks_discover_to_record_and_prints_counts(self) -> None:
        seeds = [
            _job(source_job_id="1", url="https://www.linkedin.com/jobs/view/1/"),
            _job(
                source_job_id="2",
                url="https://www.linkedin.com/jobs/view/2/",
                title="Wrong title",
            ),
            _job(
                source_job_id="3",
                url="https://www.linkedin.com/jobs/view/3/",
                title="Confirm me",
            ),
            _job(
                source_job_id="4",
                url="https://www.linkedin.com/jobs/view/4/",
                easy_apply=False,
                apply_url="https://ats.example/4",
            ),
        ]
        invoked: list[str] = []
        dry_runs: list[bool | None] = []

        def discover_fn(query: str, locations: Any, **kwargs: Any) -> DiscoverResult:
            self.assertEqual(query, "ServiceNow developer")
            self.assertEqual(list(locations), ["Spain"])
            self.assertTrue(kwargs.get("easy_apply_only"))
            return DiscoverResult(jobs=list(seeds), duplicate=2)

        def invoke_fn(seed: JobState, **kwargs: Any) -> JobState:
            invoked.append(str(seed.get("source_job_id")))
            dry_runs.append(kwargs.get("dry_run"))
            job_id = str(seed.get("source_job_id"))
            finished = dict(seed)
            if job_id == "1":
                finished.update(outcome="accept", apply_status="dry_run_ready")
            elif job_id == "2":
                finished.update(outcome="reject", apply_status="skipped")
            elif job_id == "3":
                finished.update(
                    outcome="needs_confirmation",
                    apply_status="manual",
                    review_reason="CEFR unknown",
                )
            else:
                finished.update(apply_status="external")
            return finished  # type: ignore[return-value]

        lines: list[str] = []
        counts = run_batch(
            "ServiceNow developer",
            ["Spain"],
            dry_run=True,
            discover_fn=discover_fn,
            invoke_fn=invoke_fn,
            output_fn=lines.append,
            skip_preflight=True,
        )

        self.assertEqual(invoked, ["1", "2", "3", "4"])
        self.assertTrue(all(flag is True for flag in dry_runs))
        self.assertEqual(counts.discovered, 4)
        self.assertEqual(counts.duplicate, 2)
        self.assertEqual(counts.rejected, 1)
        self.assertEqual(counts.review, 1)
        self.assertEqual(counts.external, 1)
        self.assertEqual(counts.dry_run_ready, 1)
        self.assertEqual(counts.applied, 0)
        self.assertIn("discovered: 4", format_counts(counts))
        self.assertIn("dry-run-ready: 1", format_counts(counts))

    def test_duplicate_jobs_are_not_invoked(self) -> None:
        invoked: list[str] = []

        def discover_fn(*_args: Any, **_kwargs: Any) -> DiscoverResult:
            return DiscoverResult(jobs=[], duplicate=5)

        def invoke_fn(seed: JobState, **_kwargs: Any) -> JobState:
            invoked.append(str(seed.get("source_job_id")))
            return seed

        counts = run_batch(
            "ServiceNow developer",
            ["Spain"],
            discover_fn=discover_fn,
            invoke_fn=invoke_fn,
            output_fn=lambda _: None,
            skip_preflight=True,
        )
        self.assertEqual(invoked, [])
        self.assertEqual(counts.discovered, 0)
        self.assertEqual(counts.duplicate, 5)

    def test_geo_id_is_passed_to_discover(self) -> None:
        seen: list[str | None] = []

        def discover_fn(*_args: Any, **kwargs: Any) -> DiscoverResult:
            filters = kwargs.get("filters")
            seen.append(getattr(filters, "geo_id", None))
            return DiscoverResult(jobs=[], duplicate=0)

        run_batch(
            "ServiceNow developer",
            ["Spain"],
            filters=SearchFilters(geo_id="91000007"),
            discover_fn=discover_fn,
            invoke_fn=lambda seed, **kw: seed,
            output_fn=lambda _: None,
            skip_preflight=True,
        )
        self.assertEqual(seen, ["91000007"])

    def test_include_external_is_passed_to_discover(self) -> None:
        seen: list[bool] = []

        def discover_fn(*_args: Any, **kwargs: Any) -> DiscoverResult:
            seen.append(bool(kwargs.get("easy_apply_only")))
            return DiscoverResult(jobs=[], duplicate=0)

        run_batch(
            "React",
            ["Spain"],
            easy_apply_only=False,
            discover_fn=discover_fn,
            invoke_fn=lambda seed, **kw: seed,
            output_fn=lambda _: None,
            skip_preflight=True,
        )
        self.assertEqual(seen, [False])


class MainTests(unittest.TestCase):
    def test_run_prints_counts(self) -> None:
        def discover_fn(*_args: Any, **_kwargs: Any) -> DiscoverResult:
            return DiscoverResult(
                jobs=[_job(source_job_id="9", url="https://www.linkedin.com/jobs/view/9/")],
                duplicate=1,
            )

        def invoke_fn(seed: JobState, **kwargs: Any) -> JobState:
            self.assertTrue(kwargs.get("dry_run"))
            finished = dict(seed)
            finished.update(outcome="accept", apply_status="dry_run_ready")
            return finished  # type: ignore[return-value]

        lines: list[str] = []
        code = main(
            [
                "run",
                "--query",
                "ServiceNow developer",
                "--location",
                "Spain",
                "--dry-run",
            ],
            output_fn=lines.append,
            discover_fn=discover_fn,
            invoke_fn=invoke_fn,
        )
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("discovered: 1", text)
        self.assertIn("duplicate: 1", text)
        self.assertIn("dry-run-ready: 1", text)
        self.assertIn("applied: 0", text)

    def test_main_forwards_geo_id_and_work_type(self) -> None:
        seen: list[SearchFilters] = []

        def discover_fn(*_args: Any, **kwargs: Any) -> DiscoverResult:
            seen.append(kwargs["filters"])
            return DiscoverResult(jobs=[], duplicate=0)

        main(
            [
                "run",
                "--query",
                "ServiceNow developer",
                "--geo-id",
                "91000007",
                "--work-type",
                "remote",
                "--dry-run",
            ],
            output_fn=lambda _: None,
            discover_fn=discover_fn,
            invoke_fn=lambda seed, **kw: seed,
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].geo_id, "91000007")
        self.assertEqual(seen[0].work_type, WORK_TYPE_CODES["remote"])

    def test_discover_does_not_invoke_graph(self) -> None:
        invoked: list[str] = []

        def discover_fn(*_args: Any, **_kwargs: Any) -> DiscoverResult:
            return DiscoverResult(jobs=[_job()], duplicate=0)

        def invoke_fn(seed: JobState, **_kwargs: Any) -> JobState:
            invoked.append("invoked")
            return seed

        lines: list[str] = []
        main(
            ["discover", "--query", "ServiceNow developer", "--location", "Spain"],
            output_fn=lines.append,
            discover_fn=discover_fn,
            invoke_fn=invoke_fn,
        )
        self.assertEqual(invoked, [])
        self.assertIn("discovered: 1", "\n".join(lines))
        self.assertIn("applied: 0", "\n".join(lines))


class SearchPlaceTests(unittest.TestCase):
    def test_geo_id_replaces_text_locations(self) -> None:
        self.assertEqual(
            _search_places(["Spain", "Remote"], SearchFilters(geo_id="91000007")),
            [""],
        )

    def test_work_type_remote_code(self) -> None:
        self.assertEqual(WORK_TYPE_CODES["remote"], "2")
