"""G1: thread-per-job LangGraph wiring and resume after a kill."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from jobsearchagent.database import get_job, init_db
from jobsearchagent.graph import build_graph, invoke_job, thread_id_for
from jobsearchagent.models import Form, JobState


def _seed(**overrides: object) -> JobState:
    state: JobState = {
        "url": "https://www.linkedin.com/jobs/view/123/",
        "source_job_id": "123",
        "title": "ServiceNow Developer",
        "company": "Example",
        "location": "Norway",
        "site": "LinkedIn Jobs",
        "raw_description": "Build ServiceNow applications.",
        "easy_apply": True,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class ThreadIdTests(unittest.TestCase):
    def test_prefers_source_job_id(self) -> None:
        self.assertEqual(thread_id_for(_seed()), "linkedin:123")

    def test_falls_back_to_canonical_url(self) -> None:
        self.assertEqual(
            thread_id_for(
                {
                    "url": (
                        "https://www.linkedin.com/jobs/view/"
                        "example-4481/?tracking=x"
                    )
                }
            ),
            "linkedin:4481",
        )


class GraphRoutingTests(unittest.TestCase):
    def test_external_job_records_without_triage(self) -> None:
        calls: list[str] = []

        def triage_fn(_state: JobState) -> dict:
            calls.append("triage")
            return {"outcome": "accept", "profile_id": "x", "fit_score": 1.0}

        recorded: list[JobState] = []

        def record_fn(state: JobState, **_kwargs: object) -> dict:
            recorded.append(dict(state))
            return {"apply_status": state.get("apply_status")}

        graph = build_graph(
            checkpointer=InMemorySaver(),
            triage_fn=triage_fn,
            record_fn=record_fn,
            form_fn=lambda state, config=None: {},
            apply_fn=lambda state, config=None: {},
        )
        result = graph.invoke(
            _seed(easy_apply=False, apply_url="https://ats.example/9"),
            {"configurable": {"thread_id": "linkedin:123"}},
        )
        self.assertEqual(calls, [])
        self.assertEqual(result.get("apply_status"), "external")
        self.assertEqual(recorded[0].get("apply_url"), "https://ats.example/9")

    def test_reject_and_needs_confirmation_skip_form(self) -> None:
        forms: list[str] = []

        def record_fn(state: JobState, **_kwargs: object) -> dict:
            return {}

        reject = build_graph(
            checkpointer=InMemorySaver(),
            triage_fn=lambda state: {
                "outcome": "reject",
                "profile_id": "x",
                "fit_score": 0.1,
            },
            record_fn=record_fn,
            form_fn=lambda state, config=None: forms.append("form") or {},
            apply_fn=lambda state, config=None: {},
            output_fn=lambda _: None,
        ).invoke(_seed(), {"configurable": {"thread_id": "linkedin:reject"}})
        confirm = build_graph(
            checkpointer=InMemorySaver(),
            triage_fn=lambda state: {
                "outcome": "needs_confirmation",
                "profile_id": "x",
                "fit_score": 0.5,
                "review_reason": "CEFR unknown",
            },
            record_fn=record_fn,
            form_fn=lambda state, config=None: forms.append("form") or {},
            apply_fn=lambda state, config=None: {},
            output_fn=lambda _: None,
        ).invoke(_seed(), {"configurable": {"thread_id": "linkedin:confirm"}})

        self.assertEqual(forms, [])
        self.assertEqual(reject.get("apply_status"), "skipped")
        self.assertEqual(confirm.get("apply_status"), "manual")
        self.assertEqual(confirm.get("review_reason"), "CEFR unknown")

    def test_accept_runs_end_to_end_to_record(self) -> None:
        order: list[str] = []

        def form_fn(_state: JobState, _config: object = None) -> dict:
            order.append("form")
            return {
                "form": Form(url="https://www.linkedin.com/jobs/view/123/"),
                "apply_status": "dry_run_ready",
            }

        def apply_fn(_state: JobState, _config: object = None) -> dict:
            order.append("apply")
            return {"apply_status": "dry_run_ready"}

        def record_fn(state: JobState, **_kwargs: object) -> dict:
            order.append("record")
            return {}

        result = build_graph(
            checkpointer=InMemorySaver(),
            triage_fn=lambda state: {
                "outcome": "accept",
                "profile_id": "servicenow-developer",
                "fit_score": 1.2,
            },
            requirements_fn=lambda state: {"required_artifacts": ["cv"]},
            form_fn=form_fn,
            apply_fn=apply_fn,
            record_fn=record_fn,
            output_fn=lambda _: None,
        ).invoke(_seed(), {"configurable": {"thread_id": "linkedin:123"}})

        self.assertEqual(order, ["form", "apply", "record"])
        self.assertEqual(result.get("apply_status"), "dry_run_ready")
        self.assertEqual(result.get("profile_id"), "servicenow-developer")

    def test_triage_prints_jev_feedback(self) -> None:
        lines: list[str] = []

        build_graph(
            checkpointer=InMemorySaver(),
            triage_fn=lambda state: {
                "outcome": "accept",
                "profile_id": "servicenow-developer",
                "fit_score": 1.4,
                "triage_details": {
                    "profile": {
                        "choice": "servicenow-developer",
                        "source": "title_and_skills",
                    },
                    "location_eligibility": {
                        "choice": "eligible",
                        "confidence": 0.9,
                        "rationale": "Spain is listed.",
                    },
                    "non_location_outcome": {"choice": "accept"},
                    "fit": {"score": 1.4},
                },
            },
            form_fn=lambda state, config=None: {"apply_status": "dry_run_ready"},
            apply_fn=lambda state, config=None: {},
            record_fn=lambda state, **kwargs: {},
            output_fn=lines.append,
        ).invoke(_seed(), {"configurable": {"thread_id": "linkedin:jev-log"}})

        text = "\n".join(lines)
        self.assertIn("JEV triage:", text)
        self.assertIn("profile=servicenow-developer", text)
        self.assertIn("location=eligible", text)
        self.assertIn("outcome=accept", text)
        self.assertIn("Spain is listed.", text)
        self.assertIn('"fit_score": 1.4', text)

    def test_invoke_resumes_after_a_kill(self) -> None:
        saver = InMemorySaver()
        order: list[str] = []
        config = {"configurable": {"thread_id": "linkedin:resume"}}
        graph = build_graph(
            checkpointer=saver,
            interrupt_before=["form"],
            triage_fn=lambda state: (
                order.append("triage")
                or {
                    "outcome": "accept",
                    "profile_id": "x",
                    "fit_score": 1.0,
                }
            ),
            requirements_fn=lambda state: (
                order.append("requirements") or {"required_artifacts": ["cv"]}
            ),
            form_fn=lambda state, config=None: (
                order.append("form") or {"apply_status": "dry_run_ready"}
            ),
            apply_fn=lambda state, config=None: {"apply_status": "dry_run_ready"},
            record_fn=lambda state, **kw: {},
            output_fn=lambda _: None,
        )

        paused = graph.invoke(_seed(), config)
        self.assertEqual(order, ["triage", "requirements"])
        self.assertIsNone(paused.get("apply_status"))

        result = graph.invoke(None, config)
        self.assertEqual(order.count("triage"), 1)
        self.assertEqual(order.count("requirements"), 1)
        self.assertIn("form", order)
        self.assertEqual(result.get("apply_status"), "dry_run_ready")

    def test_invoke_job_persists_through_sqlite_checkpointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.db"
            init_db(db)
            result = invoke_job(
                _seed(easy_apply=False, apply_url="https://ats.example/123"),
                database_path=db,
                form_fn=lambda state, config=None: {},
                apply_fn=lambda state, config=None: {},
                output_fn=lambda _: None,
            )
            stored = get_job("123", path=db)
        self.assertEqual(result.get("apply_status"), "external")
        self.assertEqual(stored and stored.get("apply_status"), "external")
