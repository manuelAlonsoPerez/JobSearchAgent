"""G2: ApplyPilot-style CLI batch runner.

``python -m jobsearchagent discover|run`` walks LinkedIn search and, for
``run``, the G1 graph through record. Counts print at the end.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .browser import session as browser_session
from .config import get_settings
from .database import init_db
from .discovery.linkedin import (
    POSTED_WITHIN_SECONDS,
    SORT_CODES,
    WORK_TYPE_CODES,
    LinkedInDiscovery,
    SearchFilters,
)
from .apply.record import record
from .graph import invoke_job
from .models import JobState


def _emit(message: str) -> None:
    print(message, flush=True)

DiscoverFn = Callable[..., "DiscoverResult"]
InvokeFn = Callable[..., JobState]


@dataclass
class DiscoverResult:
    jobs: list[JobState] = field(default_factory=list)
    duplicate: int = 0


@dataclass
class RunCounts:
    discovered: int = 0
    duplicate: int = 0
    rejected: int = 0
    review: int = 0
    external: int = 0
    dry_run_ready: int = 0
    applied: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "duplicate": self.duplicate,
            "rejected": self.rejected,
            "review": self.review,
            "external": self.external,
            "dry-run-ready": self.dry_run_ready,
            "applied": self.applied,
        }


def format_counts(counts: RunCounts) -> str:
    """One line per G2 count, in the guide's order."""

    return "\n".join(f"{name}: {value}" for name, value in counts.as_dict().items())


def classify_result(state: JobState) -> str:
    """Map a finished job onto one G2 outcome bucket."""

    status = state.get("apply_status")
    if status == "applied":
        return "applied"
    if status == "dry_run_ready":
        return "dry_run_ready"
    if status == "external":
        return "external"
    if status == "skipped" and state.get("outcome") == "reject":
        return "rejected"
    return "review"


def tally(counts: RunCounts, state: JobState) -> str:
    bucket = classify_result(state)
    if bucket == "applied":
        counts.applied += 1
    elif bucket == "dry_run_ready":
        counts.dry_run_ready += 1
    elif bucket == "external":
        counts.external += 1
    elif bucket == "rejected":
        counts.rejected += 1
    else:
        counts.review += 1
    return bucket


def discover_jobs(
    query: str,
    locations: Sequence[str],
    *,
    limit: int = 20,
    easy_apply_only: bool = True,
    filters: SearchFilters | None = None,
    database_path: str | Path | None = None,
    output_fn: Callable[[str], None] = _emit,
    input_fn: Callable[[str], str] = input,
) -> DiscoverResult:
    """Search LinkedIn and persist unseen jobs. Skips jobs already in SQLite."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    search = filters or SearchFilters(easy_apply=easy_apply_only)
    places = _search_places(locations, search)
    init_db(database_path)
    stats: dict[str, int] = {"duplicate": 0}
    discovery = LinkedInDiscovery(input_fn=input_fn, output_fn=output_fn)
    jobs: list[JobState] = []
    remaining = limit
    with browser_session(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        discovery.ensure_login(page)
        for index, location in enumerate(places):
            share, extra = divmod(remaining, len(places) - index)
            search_limit = share + extra
            if search_limit < 1:
                break
            found = discovery.discover_with_page(
                page,
                query,
                location,
                search_limit,
                filters=search,
                stats=stats,
            )
            jobs.extend(found)
            remaining = max(0, remaining - len(found))
    return DiscoverResult(jobs=jobs, duplicate=int(stats.get("duplicate") or 0))


def run_batch(
    query: str,
    locations: Sequence[str],
    *,
    limit: int = 20,
    dry_run: bool | None = True,
    easy_apply_only: bool = True,
    filters: SearchFilters | None = None,
    database_path: str | Path | None = None,
    output_fn: Callable[[str], None] = _emit,
    input_fn: Callable[[str], str] = input,
    discover_fn: DiscoverFn | None = None,
    invoke_fn: InvokeFn | None = None,
    skip_preflight: bool = False,
) -> RunCounts:
    """Discover jobs, run the G1 graph on each, and return G2 counts."""

    if not skip_preflight:
        _preflight_jev(output_fn)
    counts = RunCounts()
    result = (discover_fn or discover_jobs)(
        query,
        locations,
        limit=limit,
        easy_apply_only=easy_apply_only,
        filters=filters,
        database_path=database_path,
        output_fn=output_fn,
        input_fn=input_fn,
    )
    counts.discovered = len(result.jobs)
    counts.duplicate = result.duplicate
    if not result.jobs:
        output_fn(
            "No new jobs to process. They may already be in the database "
            "(discovery skips jobs it has seen)."
        )
        return counts

    invoke = invoke_fn or invoke_job
    total = len(result.jobs)
    for index, seed in enumerate(result.jobs, start=1):
        output_fn(f"\n[{index}/{total}] {seed.get('title') or seed.get('url')}")
        try:
            finished = invoke(
                seed,
                database_path=database_path,
                output_fn=output_fn,
                dry_run=dry_run,
            )
        except Exception as exc:
            reason = f"Pipeline failed: {exc}"
            output_fn(reason + " Queued for review; continuing.")
            finished = dict(seed)
            finished["apply_status"] = "failed"
            finished["review_reason"] = reason
            try:
                record(finished, path=database_path)
            except Exception as persist_exc:
                output_fn(f"Could not persist failed job: {persist_exc}")
        bucket = tally(counts, finished)
        output_fn(
            f"  {finished.get('outcome') or '-'} → "
            f"{finished.get('apply_status') or bucket}"
        )
    return counts


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jobsearchagent",
        description=(
            "Discover LinkedIn jobs and run the Easy Apply graph. "
            "Default is dry-run: fill and stop at Review."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("discover", "Search LinkedIn and persist unseen jobs."),
        ("run", "Discover, then walk each job through the apply graph."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        _add_shared_flags(sub)
    serve = subparsers.add_parser(
        "serve",
        help="Local JSON API for the Vite jobs / review-queue UI.",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Default: 127.0.0.1 (localhost only).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port. Default: 8765.",
    )
    serve.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override DB_PATH from .env.",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    output_fn: Callable[[str], None] = _emit,
    discover_fn: DiscoverFn | None = None,
    invoke_fn: InvokeFn | None = None,
) -> int:
    arguments = parse_args(argv)
    if arguments.command == "serve":
        from .web import run_server

        run_server(
            host=arguments.host,
            port=arguments.port,
            db_path=arguments.db_path,
        )
        return 0

    locations = arguments.locations
    dry_run = True if arguments.dry_run else None
    filters = _filters_from_args(arguments)
    if arguments.command == "discover":
        result = (discover_fn or discover_jobs)(
            arguments.query,
            locations or _locations(None),
            limit=arguments.limit,
            easy_apply_only=arguments.easy_apply_only,
            filters=filters,
            database_path=arguments.db_path,
            output_fn=output_fn,
        )
        for index, job in enumerate(result.jobs, start=1):
            output_fn(
                f"[{index}] {job.get('title') or 'Untitled'} — "
                f"{job.get('company') or '?'} ({job.get('url')})"
            )
        counts = RunCounts(
            discovered=len(result.jobs),
            duplicate=result.duplicate,
        )
        output_fn(format_counts(counts))
        return 0

    counts = run_batch(
        arguments.query,
        locations or _locations(None),
        limit=arguments.limit,
        dry_run=dry_run,
        easy_apply_only=arguments.easy_apply_only,
        filters=filters,
        database_path=arguments.db_path,
        output_fn=output_fn,
        discover_fn=discover_fn,
        invoke_fn=invoke_fn,
        skip_preflight=discover_fn is not None,
    )
    output_fn("")
    output_fn(format_counts(counts))
    return 0


def _add_shared_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--query",
        default="ServiceNow developer",
        help='LinkedIn search query. Default: "ServiceNow developer".',
    )
    parser.add_argument(
        "--location",
        action="append",
        dest="locations",
        help="Search location. Repeat for several. Default: JOB_LOCATIONS.",
    )
    parser.add_argument(
        "--geo-id",
        dest="geo_id",
        default=None,
        help=(
            "LinkedIn region geoId instead of a text location, "
            "e.g. EMEA = 91000007. Overrides --location for the search."
        ),
    )
    parser.add_argument(
        "--posted-within",
        choices=sorted(POSTED_WITHIN_SECONDS),
        default="week",
        help="Recency filter (f_TPR). Default: week.",
    )
    parser.add_argument(
        "--work-type",
        choices=[*sorted(WORK_TYPE_CODES), "any"],
        default="remote",
        help="Work-type filter (f_WT). 'any' removes it. Default: remote.",
    )
    parser.add_argument(
        "--sort",
        choices=sorted(SORT_CODES),
        default="relevance",
        help="Result ordering (sortBy). Default: relevance.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum new jobs to collect. Default: 20.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill Easy Apply, close without Submit, Save, and queue.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--easy-apply-only",
        dest="easy_apply_only",
        action="store_true",
        help="Restrict search to LinkedIn Easy Apply (default).",
    )
    group.add_argument(
        "--include-external",
        dest="easy_apply_only",
        action="store_false",
        help="Also collect jobs that open an external apply URL.",
    )
    parser.set_defaults(easy_apply_only=True)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override DB_PATH from .env.",
    )


def _filters_from_args(arguments: argparse.Namespace) -> SearchFilters:
    return SearchFilters(
        geo_id=arguments.geo_id,
        posted_within_seconds=POSTED_WITHIN_SECONDS[arguments.posted_within],
        work_type=(
            None
            if arguments.work_type == "any"
            else WORK_TYPE_CODES[arguments.work_type]
        ),
        easy_apply=arguments.easy_apply_only,
        sort=SORT_CODES[arguments.sort],
    )


def _search_places(locations: Sequence[str], filters: SearchFilters) -> list[str]:
    """A geoId is one region search; otherwise iterate text locations."""

    if filters.geo_id:
        return [""]
    return _locations(locations)


def _locations(locations: Sequence[str] | None) -> list[str]:
    if locations:
        cleaned = [str(item).strip() for item in locations if str(item).strip()]
        if cleaned:
            return cleaned
    return list(get_settings().job_locations)


def _preflight_jev(output_fn: Callable[[str], None]) -> None:
    base = get_settings().ollama_base_url.rstrip("/")
    try:
        httpx.get(f"{base}/api/tags", timeout=5.0).raise_for_status()
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim
        raise RuntimeError(
            f"Ollama is not reachable at {base} ({exc}). Start it with "
            "`ollama serve` before running the agent."
        ) from exc
    output_fn(f"Ollama reachable at {base}.")


if __name__ == "__main__":
    raise SystemExit(main())
