"""Production LinkedIn discovery-and-triage agent."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from .browser import session
from .config import get_settings
from .database import add_to_review, init_db, record_result
from .discovery.linkedin import LinkedInDiscovery
from .jev import Jev
from .models import JobState
from .triage import triage


class LinkedInAgent:
    """Run the tested LinkedIn discovery and JEV triage as a durable pipeline."""

    def __init__(
        self,
        *,
        output_fn: Callable[[str], None] = print,
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
    ) -> list[JobState]:
        """Discover, triage, route, and persist jobs without submitting forms."""

        if max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")
        selected_locations = locations or self.locations
        if not selected_locations:
            raise ValueError("At least one location is required")

        init_db()
        discovered: list[JobState] = []
        base_limit, remainder = divmod(max_jobs, len(selected_locations))
        try:
            with session(headless=False) as context:
                page = context.pages[0] if context.pages else context.new_page()
                self.discovery.ensure_login(page)
                for index, location in enumerate(selected_locations):
                    location_limit = base_limit + (1 if index < remainder else 0)
                    if location_limit == 0:
                        continue
                    discovered.extend(
                        self.discovery.discover_with_page(
                            page,
                            query,
                            location,
                            location_limit,
                        )
                    )

            results: list[JobState] = []
            for index, state in enumerate(discovered, start=1):
                self._print_job(index, len(discovered), state)
                try:
                    update = triage(
                        state,
                        jev=self.jev,
                        locations=selected_locations,
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
                self._route(state)
                record_result(state)
                self._print_decision(state, details)
                results.append(state)
            return results
        finally:
            if self._owns_jev:
                self.jev.close()

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
            "profile_id": state.get("profile_id"),
            "outcome": state.get("outcome"),
            "fit_score": state.get("fit_score"),
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
    arguments = parser.parse_args()
    LinkedInAgent().run(
        arguments.query,
        max_jobs=arguments.max_jobs,
        locations=arguments.locations,
    )


if __name__ == "__main__":
    main()
