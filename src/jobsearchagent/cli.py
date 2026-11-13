"""G2: batch runner for the enabled sites in ``sites.yaml``.

``python -m jobsearchagent discover|run`` walks each enabled site that has
an adapter. LinkedIn keeps its current search. ``run`` then hands each new
job to the G1 graph. Counts print at the end.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .browser import session as browser_session
from .config import Site, enabled_sites, get_settings, refuse_linkedin_automation, search_queries
from .database import init_db
from .discovery.linkedin import (
    POSTED_WITHIN_SECONDS,
    SORT_CODES,
    WORK_TYPE_CODES,
    LinkedInDiscovery,
    SearchFilters,
    parse_month_count,
    posted_window_seconds,
)
from .discovery.loop import SearchUnit, search_units, select_sites, skip_reason
from .discovery.registry import SearchFilters as AdapterFilters
from .discovery.registry import adapter_for, discover_with_adapter
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
    if status == "rejected":
        return "rejected"
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


def jobs_this_search(
    per_search: int,
    collected: int,
    max_jobs: int | None,
) -> int:
    """How many new jobs the next query×location may collect."""

    if max_jobs is None:
        return per_search
    remaining = max_jobs - collected
    if remaining < 1:
        return 0
    return min(per_search, remaining)


def discover_jobs(
    query: str | Sequence[str] | None,
    locations: Sequence[str],
    *,
    limit: int = 20,
    easy_apply_only: bool = True,
    filters: SearchFilters | None = None,
    database_path: str | Path | None = None,
    output_fn: Callable[[str], None] = _emit,
    input_fn: Callable[[str], str] = input,
    max_tier: int | None = None,
    max_jobs: int | None = None,
) -> DiscoverResult:
    """Search LinkedIn and persist unseen jobs. Skips jobs already in SQLite.

    ``query`` may be one string, several strings, or omitted. When omitted,
    queries come from ``searches.yaml`` (optionally capped with ``max_tier``).
    One browser session covers every query × location pair. ``limit`` is new
    jobs per pair; ``max_jobs`` optionally caps the whole run.
    """

    if limit < 1:
        raise ValueError("limit must be at least 1")
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("max_jobs must be at least 1")
    refuse_linkedin_automation()
    queries = normalize_search_queries(query, max_tier=max_tier)
    init_db(database_path)
    stats: dict[str, int] = {"duplicate": 0}
    with browser_session(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        jobs = _collect_linkedin(
            page,
            queries,
            locations,
            limit=limit,
            easy_apply_only=easy_apply_only,
            filters=filters,
            max_jobs=max_jobs,
            collected=0,
            output_fn=output_fn,
            input_fn=input_fn,
            stats=stats,
        )
    return DiscoverResult(jobs=jobs, duplicate=int(stats.get("duplicate") or 0))


def discover_enabled_sites(
    query: str | Sequence[str] | None,
    locations: Sequence[str],
    *,
    limit: int = 20,
    easy_apply_only: bool = True,
    filters: SearchFilters | None = None,
    database_path: str | Path | None = None,
    output_fn: Callable[[str], None] = _emit,
    input_fn: Callable[[str], str] = input,
    max_tier: int | None = None,
    max_jobs: int | None = None,
    site_ids: Sequence[str] | None = None,
    skip_site_ids: Sequence[str] | None = None,
    month: int | None = None,
    work_type: str | None = None,
    sites: Sequence[Site] | None = None,
    session_fn: Callable[..., Any] | None = None,
    adapters: Mapping[str, Any] | None = None,
) -> DiscoverResult:
    """Walk enabled sites. A missing adapter or a site error does not stop the run."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("max_jobs must be at least 1")
    queries = normalize_search_queries(query, max_tier=max_tier)
    places = _locations(locations)
    chosen, missing = select_sites(
        list(sites) if sites is not None else enabled_sites(),
        list(site_ids) if site_ids else None,
        list(skip_site_ids) if skip_site_ids else None,
    )
    for site_id in missing:
        output_fn(f"{site_id} skipped: it is not in the enabled set.")
    runnable: list[tuple[Site, Any]] = []
    for site in chosen:
        reason = skip_reason(site, month=month, work_type=work_type)
        if reason:
            output_fn(reason)
            continue
        adapter = _adapter_for_run(site, adapters)
        if adapter is None:
            output_fn(
                f"{site.id} skipped: no discovery adapter registered "
                f"for '{site.adapter}'."
            )
            continue
        if site.adapter == "linkedin":
            try:
                refuse_linkedin_automation()
            except RuntimeError as exc:
                output_fn(f"{site.id} failed: {exc} Continuing with the next site.")
                continue
        runnable.append((site, adapter))
    if not runnable:
        return DiscoverResult()

    init_db(database_path)
    output_fn(
        "Walking "
        + ", ".join(site.id for site, _adapter in runnable)
        + f" (up to {limit} new jobs per unit"
        + (f", stop after {max_jobs}" if max_jobs is not None else "")
        + ")"
    )
    stats: dict[str, int] = {"duplicate": 0}
    jobs: list[JobState] = []
    opener = session_fn or browser_session
    with opener(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        for site, adapter in runnable:
            if max_jobs is not None and len(jobs) >= max_jobs:
                output_fn(f"Reached --max-jobs {max_jobs}; stopping discovery.")
                break
            try:
                jobs.extend(
                    _discover_site(
                        site,
                        adapter,
                        page,
                        queries,
                        places,
                        limit=limit,
                        easy_apply_only=easy_apply_only,
                        filters=filters,
                        month=month,
                        work_type=work_type,
                        max_jobs=max_jobs,
                        collected=len(jobs),
                        output_fn=output_fn,
                        input_fn=input_fn,
                        stats=stats,
                    )
                )
            except Exception as exc:
                output_fn(
                    f"{site.id} failed: {exc} Continuing with the next site."
                )
    return DiscoverResult(jobs=jobs, duplicate=int(stats.get("duplicate") or 0))


def _collect_linkedin(
    page: Any,
    queries: Sequence[str],
    locations: Sequence[str],
    *,
    limit: int,
    easy_apply_only: bool,
    filters: SearchFilters | None,
    max_jobs: int | None,
    collected: int,
    output_fn: Callable[[str], None],
    input_fn: Callable[[str], str],
    stats: dict[str, int],
) -> list[JobState]:
    """Run today's LinkedIn query × location loop on an open page."""

    search = filters or SearchFilters(easy_apply=easy_apply_only)
    places = _search_places(locations, search)
    pairs = [(item, place) for item in queries for place in places]
    cap = f", stop after {max_jobs} new jobs" if max_jobs is not None else ""
    output_fn(
        f"{len(queries)} search query(ies) × {len(places)} location(s) "
        f"({len(pairs)} LinkedIn searches, up to {limit} new jobs each{cap})"
    )
    discovery = LinkedInDiscovery(input_fn=input_fn, output_fn=output_fn)
    discovery.ensure_login(page)
    jobs: list[JobState] = []
    for index, (search_query, location) in enumerate(pairs):
        search_limit = jobs_this_search(limit, collected + len(jobs), max_jobs)
        if search_limit < 1:
            output_fn(f"Reached --max-jobs {max_jobs}; stopping discovery.")
            break
        place_label = location or "geoId"
        output_fn(
            f"Query {index + 1}/{len(pairs)}: {search_query!r} ({place_label})"
        )
        found = discovery.discover_with_page(
            page,
            search_query,
            location,
            search_limit,
            filters=search,
            stats=stats,
        )
        jobs.extend(found)
    return jobs


def _discover_site(
    site: Site,
    adapter: Any,
    page: Any,
    queries: Sequence[str],
    locations: Sequence[str],
    *,
    limit: int,
    easy_apply_only: bool,
    filters: SearchFilters | None,
    month: int | None,
    work_type: str | None,
    max_jobs: int | None,
    collected: int,
    output_fn: Callable[[str], None],
    input_fn: Callable[[str], str],
    stats: dict[str, int],
) -> list[JobState]:
    if site.adapter == "linkedin":
        return _collect_linkedin(
            page,
            queries,
            locations,
            limit=limit,
            easy_apply_only=easy_apply_only,
            filters=filters,
            max_jobs=max_jobs,
            collected=collected,
            output_fn=output_fn,
            input_fn=input_fn,
            stats=stats,
        )
    found: list[JobState] = []
    units = search_units(site, list(queries), list(locations))
    output_fn(f"{site.id}: {len(units)} search unit(s)")
    for unit in units:
        search_limit = jobs_this_search(limit, collected + len(found), max_jobs)
        if search_limit < 1:
            output_fn(f"Reached --max-jobs {max_jobs}; stopping discovery.")
            break
        _log_unit(output_fn, site.id, unit)
        batch = discover_with_adapter(
            adapter,
            page,
            site,
            unit.query,
            unit.location,
            search_limit,
            _adapter_filters(month, work_type),
        )
        found.extend(batch[:search_limit])
    return found


def _adapter_filters(month: int | None, work_type: str | None) -> AdapterFilters:
    filters: AdapterFilters = {}
    if month is not None:
        filters["month"] = month
    if work_type:
        filters["work_type"] = work_type
    return filters


def _adapter_for_run(site: Site, adapters: Mapping[str, Any] | None) -> Any | None:
    if adapters is not None:
        return adapters.get(site.adapter)
    try:
        return adapter_for(site.adapter, site_id=site.id)
    except RuntimeError:
        return None


def _log_unit(output_fn: Callable[[str], None], site_id: str, unit: SearchUnit) -> None:
    query = unit.query if unit.query is not None else "no query"
    location = unit.location if unit.location is not None else "no location"
    output_fn(f"  {site_id}: {query} / {location}")


def run_batch(
    query: str | Sequence[str] | None,
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
    max_tier: int | None = None,
    max_jobs: int | None = None,
    site_ids: Sequence[str] | None = None,
    skip_site_ids: Sequence[str] | None = None,
    month: int | None = None,
    work_type: str | None = None,
    sites: Sequence[Site] | None = None,
    session_fn: Callable[..., Any] | None = None,
    adapters: Mapping[str, Any] | None = None,
) -> RunCounts:
    """Discover jobs, run the G1 graph on each, and return G2 counts."""

    if not skip_preflight:
        _preflight_jev(output_fn)
    counts = RunCounts()
    queries = normalize_search_queries(query, max_tier=max_tier)
    if discover_fn is not None:
        result = discover_fn(
            queries,
            locations,
            limit=limit,
            easy_apply_only=easy_apply_only,
            filters=filters,
            database_path=database_path,
            output_fn=output_fn,
            input_fn=input_fn,
            max_tier=max_tier,
            max_jobs=max_jobs,
        )
    else:
        result = discover_enabled_sites(
            queries,
            locations,
            limit=limit,
            easy_apply_only=easy_apply_only,
            filters=filters,
            database_path=database_path,
            output_fn=output_fn,
            input_fn=input_fn,
            max_tier=max_tier,
            max_jobs=max_jobs,
            site_ids=site_ids,
            skip_site_ids=skip_site_ids,
            month=month,
            work_type=work_type,
            sites=sites,
            session_fn=session_fn,
            adapters=adapters,
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
            "Discover jobs on the enabled sites and run the apply graph. "
            "Default is dry-run: fill LinkedIn Easy Apply and stop at Review."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("discover", "Search the enabled sites and persist unseen jobs."),
        ("run", "Discover, then walk each new job through the apply graph."),
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
    queries = normalize_search_queries(
        arguments.queries,
        max_tier=arguments.tier,
    )
    if arguments.command == "discover":
        result = (discover_fn or discover_enabled_sites)(
            queries,
            locations or _locations(None),
            limit=arguments.limit,
            easy_apply_only=arguments.easy_apply_only,
            filters=filters,
            database_path=arguments.db_path,
            output_fn=output_fn,
            max_tier=arguments.tier,
            max_jobs=arguments.max_jobs,
            site_ids=arguments.site_ids,
            skip_site_ids=arguments.skip_site_ids,
            month=arguments.month,
            work_type=arguments.work_type,
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
        queries,
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
        max_tier=arguments.tier,
        max_jobs=arguments.max_jobs,
        site_ids=arguments.site_ids,
        skip_site_ids=arguments.skip_site_ids,
        month=arguments.month,
        work_type=arguments.work_type,
    )
    output_fn("")
    output_fn(format_counts(counts))
    return 0


def _add_shared_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help=(
            "LinkedIn search query. Repeat for several. "
            "Omit to run every query in searches.yaml."
        ),
    )
    parser.add_argument(
        "--tier",
        type=int,
        default=None,
        metavar="N",
        help=(
            "When --query is omitted, only searches.yaml queries at this "
            "tier or below (1 = exact)."
        ),
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
        "--month",
        type=_month_arg,
        default=None,
        metavar="N",
        help=(
            "Only jobs published in the last N months. "
            "Overrides --posted-within. 1 is LinkedIn's Past month."
        ),
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
        help="Maximum new jobs per search unit. Default: 20.",
    )
    parser.add_argument(
        "--max-jobs",
        dest="max_jobs",
        type=int,
        default=None,
        help=(
            "Optional cap on new jobs for the whole run. "
            "Omit to walk every search unit."
        ),
    )
    parser.add_argument(
        "--site",
        action="append",
        dest="site_ids",
        default=None,
        help=(
            "Enabled site id to include. Repeat to name several. "
            "Omit to walk every enabled site."
        ),
    )
    parser.add_argument(
        "--skip-site",
        action="append",
        dest="skip_site_ids",
        default=None,
        help="Enabled site id to leave out of this run. Repeatable.",
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


def _month_arg(value: str) -> int:
    try:
        return parse_month_count(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _filters_from_args(arguments: argparse.Namespace) -> SearchFilters:
    return SearchFilters(
        geo_id=arguments.geo_id,
        posted_within_seconds=posted_window_seconds(
            arguments.posted_within,
            arguments.month,
        ),
        work_type=(
            None
            if arguments.work_type == "any"
            else WORK_TYPE_CODES[arguments.work_type]
        ),
        easy_apply=arguments.easy_apply_only,
        sort=SORT_CODES[arguments.sort],
    )


def normalize_search_queries(
    query: str | Sequence[str] | None,
    *,
    max_tier: int | None = None,
) -> list[str]:
    """Use explicit --query values, or the configured searches.yaml list."""

    if isinstance(query, str):
        query = [query]
    if query:
        cleaned = [str(item).strip() for item in query if str(item).strip()]
        if cleaned:
            return cleaned
    return search_queries(max_tier=max_tier)


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
