"""Production LinkedIn discovery-and-triage agent."""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from typing import Any

import httpx

from .browser import session
from .config import get_settings
from .database import add_to_review, init_db, record_result
from .discovery.linkedin import (
    POSTED_WITHIN_SECONDS,
    SORT_CODES,
    WORK_TYPE_CODES,
    LinkedInDiscovery,
    SearchFilters,
)
from .jev import Jev
from .models import JobState
from .triage import decide_requirements, triage


def _emit(message: str) -> None:
    """Print progress and flush immediately so it is not swallowed by buffering."""

    print(message, flush=True)


class LinkedInAgent:
    """Run the tested LinkedIn discovery and JEV triage as a durable pipeline."""

    def __init__(
        self,
        *,
        output_fn: Callable[[str], None] = _emit,
        input_fn: Callable[[str], str] = input,
        jev: Jev | None = None,
    ) -> None:
        self.output = output_fn
        self.locations = get_settings().job_locations
        self.discovery = LinkedInDiscovery(
            input_fn=input_fn,
            output_fn=output_fn,
        )
        self.jev = jev or Jev()
        self._owns_jev = jev is None

    def run(
        self,
        query: str,
        *,
        max_jobs: int = 5,
        locations: list[str] | None = None,
        geo_id: str | None = None,
        filters: SearchFilters | None = None,
    ) -> list[JobState]:
        """Discover, triage, route, and persist jobs without submitting forms."""

        if max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")
        base_filters = filters or SearchFilters()

        # Location eligibility in triage always follows JOB_LOCATIONS (or an
        # explicit --location override), independent of the search region.
        triage_locations = locations or self.locations
        if not triage_locations:
            raise ValueError("At least one location is required")

        # A region geoId replaces the per-location text searches with one scoped
        # search; otherwise iterate the configured/overridden locations.
        if geo_id:
            searches: list[tuple[str, SearchFilters]] = [
                ("", replace(base_filters, geo_id=geo_id))
            ]
        else:
            searches = [
                (location, replace(base_filters, geo_id=None))
                for location in triage_locations
            ]

        discovered: list[JobState] = []
        base_limit, remainder = divmod(max_jobs, len(searches))
        try:
            self._preflight_jev()
            init_db()
            with session(headless=False) as context:
                page = context.pages[0] if context.pages else context.new_page()
                self.discovery.ensure_login(page)
                for index, (location, search_filters) in enumerate(searches):
                    search_limit = base_limit + (1 if index < remainder else 0)
                    if search_limit == 0:
                        continue
                    discovered.extend(
                        self.discovery.discover_with_page(
                            page,
                            query,
                            location,
                            search_limit,
                            filters=search_filters,
                        )
                    )

            if not discovered:
                self.output(
                    "\nNo new jobs to analyze. They may already be in the "
                    "database (discovery skips jobs it has seen). Clear them "
                    "with `python -m scripts.delete_jobs` to re-analyze."
                )
                return []

            results: list[JobState] = []
            for index, state in enumerate(discovered, start=1):
                self._print_job(index, len(discovered), state)
                try:
                    with self._heartbeat("JEV triage"):
                        update = triage(
                            state,
                            jev=self.jev,
                            locations=triage_locations,
                            progress=self.output,
                        )
                except Exception as exc:
                    failed = JobState(
                        **state,
                        apply_status="failed",
                        review_reason=f"Triage failed: {exc}",
                    )
                    record_result(failed)
                    add_to_review(
                        str(failed["url"]),
                        str(failed["review_reason"]),
                        str(failed.get("apply_url") or failed["url"]),
                    )
                    self.output(f"Triage failed; queued for review: {exc}")
                    results.append(failed)
                    continue

                details = update.pop("triage_details", {})
                state.update(update)
                if isinstance(details, Mapping):
                    if details.get("location_eligibility") is not None:
                        state["location_eligibility"] = details["location_eligibility"]
                    skill = details.get("skills_outcome")
                    if skill is None:
                        skill = details.get("non_location_outcome")
                    if skill is not None:
                        state["skills_outcome"] = skill
                state.update(
                    decide_requirements(
                        state,
                        jev=self.jev,
                    )
                )
                self._route(state)
                record_result(state)
                self._print_decision(state, details)
                results.append(state)
            return results
        finally:
            if self._owns_jev:
                self.jev.close()

    def _preflight_jev(self) -> None:
        """Fail fast (instead of a long silent block) if Ollama is unreachable."""

        base = get_settings().ollama_base_url.rstrip("/")
        try:
            httpx.get(f"{base}/api/tags", timeout=5.0).raise_for_status()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            raise RuntimeError(
                f"Ollama is not reachable at {base} ({exc}). Start it with "
                "`ollama serve` before running the agent."
            ) from exc
        self.output(
            f"Ollama reachable at {base}. Note: JEV runs locally on CPU, so each "
            "job can take a few minutes; progress updates print while it works."
        )

    @contextlib.contextmanager
    def _heartbeat(self, label: str, interval: float = 15.0) -> Iterator[None]:
        """Emit an elapsed-time tick while a blocking call runs, so it is not
        mistaken for a hang."""

        stop = threading.Event()
        start = time.monotonic()

        def tick() -> None:
            while not stop.wait(interval):
                elapsed = int(time.monotonic() - start)
                self.output(f"  …{label} still working ({elapsed}s elapsed)")

        thread = threading.Thread(target=tick, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1.0)

    def _route(self, state: JobState) -> None:
        outcome = state.get("outcome")
        if outcome == "reject":
            state["apply_status"] = "skipped"
            return
        if not state.get("easy_apply"):
            reason = "External application requires manual review."
            state["apply_status"] = "external"
            state["review_reason"] = reason
            add_to_review(
                str(state["url"]),
                reason,
                str(state.get("apply_url") or state["url"]),
            )
            return
        if outcome == "needs_confirmation":
            reason = state.get("review_reason") or "Candidate fact requires confirmation."
            state["apply_status"] = "manual"
            state["review_reason"] = reason
            add_to_review(
                str(state["url"]),
                reason,
                str(state.get("apply_url") or state["url"]),
            )
            return
        state["apply_status"] = "dry_run_ready"

    def _print_job(self, index: int, total: int, state: JobState) -> None:
        self.output("\n" + "=" * 78)
        self.output(f"[{index}/{total}] {state.get('title', 'Unknown title')}")
        self.output(f"Company: {state.get('company', 'Unknown company')}")
        self.output(f"Location: {state.get('location', 'Unknown location')}")
        self.output(f"URL: {state.get('url', '')}")
        description = str(state.get("raw_description", ""))
        self.output(f"Description extracted: {len(description)} characters")

    def _print_decision(
        self,
        state: JobState,
        details: Any,
    ) -> None:
        result = {
            "url": state.get("url"),
            "title": state.get("title"),
            "company": state.get("company"),
            "location": state.get("location"),
            "profile_id": state.get("profile_id"),
            "outcome": state.get("outcome"),
            "required_artifacts": state.get("required_artifacts", []),
            "apply_status": state.get("apply_status"),
            "review_reason": state.get("review_reason"),
            "details": details,
        }
        self.output(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Discover and triage LinkedIn jobs. This phase does not submit "
            "applications."
        )
    )
    parser.add_argument("--query", default="ServiceNow")
    parser.add_argument("--max-jobs", type=int, default=5)
    parser.add_argument(
        "--location",
        action="append",
        dest="locations",
        help="Override JOB_LOCATIONS; repeat for multiple locations.",
    )
    parser.add_argument(
        "--geo-id",
        dest="geo_id",
        default=None,
        help=(
            "LinkedIn region geoId to search instead of a text location, "
            "e.g. EMEA = 91000007. Overrides --location for the search "
            "(triage eligibility still follows JOB_LOCATIONS/--location)."
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
        "--easy-apply",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restrict to Easy Apply jobs (f_AL). Use --no-easy-apply to widen.",
    )
    arguments = parser.parse_args()

    filters = SearchFilters(
        posted_within_seconds=POSTED_WITHIN_SECONDS[arguments.posted_within],
        work_type=(
            None
            if arguments.work_type == "any"
            else WORK_TYPE_CODES[arguments.work_type]
        ),
        easy_apply=arguments.easy_apply,
        sort=SORT_CODES[arguments.sort],
    )
    LinkedInAgent().run(
        arguments.query,
        max_jobs=arguments.max_jobs,
        locations=arguments.locations,
        geo_id=arguments.geo_id,
        filters=filters,
    )


if __name__ == "__main__":
    main()
