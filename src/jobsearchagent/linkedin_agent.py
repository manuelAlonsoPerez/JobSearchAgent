"""Production LinkedIn discovery-and-triage agent."""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from .browser import session
from .config import get_settings
from .database import add_to_review, init_db, record_result
from .discovery.linkedin import LinkedInDiscovery
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
    ) -> list[JobState]:
        """Discover, triage, route, and persist jobs without submitting forms."""

        if max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")
        selected_locations = locations or self.locations
        if not selected_locations:
            raise ValueError("At least one location is required")

        discovered: list[JobState] = []
        base_limit, remainder = divmod(max_jobs, len(selected_locations))
        try:
            self._preflight_jev()
            init_db()
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
            "profile_id": state.get("profile_id"),
            "outcome": state.get("outcome"),
            "fit_score": state.get("fit_score"),
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
    arguments = parser.parse_args()
    LinkedInAgent().run(
        arguments.query,
        max_jobs=arguments.max_jobs,
        locations=arguments.locations,
    )


if __name__ == "__main__":
    main()
