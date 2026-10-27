"""LangGraph wiring for one LinkedIn job (G1).

Thread id is ``linkedin:{source_job_id}`` with a canonical URL fallback.
The checkpointer is ``SqliteSaver`` so ``invoke`` can resume after a kill.

Browser pages are not part of ``JobState``. The form node keeps a process-local
session that the apply node consumes. After a process kill the fill must be
re-run; earlier triage/requirements checkpoints still resume.

CV and cover-letter writers (A4) and the validator (A5) are omitted for now.
Easy Apply uploads the existing profile PDF from ``src/docs``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import RunnableConfig

from .apply.answers import resolve_cv_path
from .apply.form import build_and_answer_form
from .apply.record import record
from .apply.submit import LINKEDIN_SITE, classify_browser_error, submit_application
from .browser import session as browser_session
from .config import load_work_profiles
from .database import get_checkpointer
from .discovery.linkedin import LinkedInDiscovery
from .models import JobState
from .triage import decide_requirements, triage
from .triage.triage import select_profile
from .utils.profile import load_profile
from .utils.text import canonicalize_url, extract_linkedin_job_id

NodeFn = Callable[..., Mapping[str, Any]]

_BROWSER_SESSIONS: dict[str, _BrowserHold] = {}


class _BrowserHold:
    def __init__(self, context_manager: Any, page: Any) -> None:
        self.context_manager = context_manager
        self.page = page

    def close(self) -> None:
        try:
            self.context_manager.__exit__(None, None, None)
        except Exception:
            pass


def thread_id_for(state: Mapping[str, Any]) -> str:
    """Return ``linkedin:{job_id}`` or ``linkedin:{canonical_url}``."""

    job_id = str(state.get("source_job_id") or "").strip()
    url = str(state.get("url") or "").strip()
    if not job_id and url:
        job_id = extract_linkedin_job_id(url) or ""
    if job_id:
        return f"linkedin:{job_id}"
    if url:
        return f"linkedin:{canonicalize_url(url)}"
    raise ValueError("A job seed needs source_job_id or url to build thread_id")


def build_graph(
    *,
    checkpointer: Any | None = None,
    database_path: str | Path | None = None,
    triage_fn: Callable[..., Mapping[str, Any]] = triage,
    requirements_fn: Callable[..., Mapping[str, Any]] = decide_requirements,
    form_fn: NodeFn | None = None,
    apply_fn: NodeFn | None = None,
    record_fn: Callable[..., Any] = record,
    output_fn: Callable[[str], None] = print,
    dry_run: bool | None = None,
    interrupt_before: list[str] | None = None,
) -> Any:
    """Compile the Phase 4 job graph."""

    fill = form_fn or (lambda state, config=None: _live_fill_form(state, config, output_fn))
    submit = apply_fn or (
        lambda state, config=None: _live_submit(
            state, config, database_path, output_fn, dry_run
        )
    )

    def triage_node(state: JobState) -> dict[str, Any]:
        result = dict(triage_fn(state))
        result.pop("triage_details", None)
        result["messages"] = [
            {"role": "system", "content": f"triage:{result.get('outcome')}"}
        ]
        return result

    def requirements_node(state: JobState) -> dict[str, Any]:
        result = dict(requirements_fn(state))
        required = list(result.get("required_artifacts") or [])
        return {
            "required_artifacts": required,
            "messages": [{"role": "system", "content": f"requirements:{required}"}],
        }

    def form_node(state: JobState, config: RunnableConfig) -> dict[str, Any]:
        return dict(fill(state, config))

    def apply_node(state: JobState, config: RunnableConfig) -> dict[str, Any]:
        return dict(submit(state, config))

    def record_node(state: JobState) -> dict[str, Any]:
        prepared = dict(state)
        prepared.update(_terminal_status(prepared))
        record_fn(prepared, path=database_path)
        return {
            "apply_status": prepared.get("apply_status"),
            "apply_url": prepared.get("apply_url"),
            "review_reason": prepared.get("review_reason"),
            "site": prepared.get("site") or LINKEDIN_SITE,
            "messages": [
                {
                    "role": "system",
                    "content": f"record:{prepared.get('apply_status')}",
                }
            ],
        }

    graph = StateGraph(JobState)
    graph.add_node("triage", triage_node)
    graph.add_node("requirements", requirements_node)
    graph.add_node("form", form_node)
    graph.add_node("apply", apply_node)
    graph.add_node("record", record_node)

    graph.add_conditional_edges(
        START,
        _route_start,
        {"triage": "triage", "record": "record"},
    )
    graph.add_conditional_edges(
        "triage",
        _route_after_triage,
        {
            "requirements": "requirements",
            "record": "record",
        },
    )
    graph.add_edge("requirements", "form")
    graph.add_edge("form", "apply")
    graph.add_edge("apply", "record")
    graph.add_edge("record", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )


def invoke_job(
    seed: JobState,
    *,
    checkpointer: Any | None = None,
    database_path: str | Path | None = None,
    thread_id: str | None = None,
    output_fn: Callable[[str], None] = print,
    dry_run: bool | None = None,
    **graph_kwargs: Any,
) -> JobState:
    """Run or resume one job thread: ``graph.invoke(seed, thread)``."""

    saver = checkpointer if checkpointer is not None else get_checkpointer(database_path)
    compiled = build_graph(
        checkpointer=saver,
        database_path=database_path,
        output_fn=output_fn,
        dry_run=dry_run,
        **graph_kwargs,
    )
    config = {"configurable": {"thread_id": thread_id or thread_id_for(seed)}}
    return compiled.invoke(seed, config)


def _route_start(state: JobState) -> Literal["triage", "record"]:
    if state.get("easy_apply") is False:
        return "record"
    return "triage"


def _route_after_triage(state: JobState) -> Literal["requirements", "record"]:
    if state.get("outcome") == "accept":
        return "requirements"
    return "record"


def _terminal_status(state: JobState) -> dict[str, Any]:
    """Fill apply_status when the job never reached the submit node."""

    if state.get("apply_status"):
        return {}
    if state.get("easy_apply") is False:
        return {
            "apply_status": "external",
            "apply_url": state.get("apply_url") or state.get("url"),
            "review_reason": (
                state.get("review_reason")
                or "External application requires manual review."
            ),
            "site": state.get("site") or LINKEDIN_SITE,
        }
    if state.get("outcome") == "reject":
        return {"apply_status": "skipped", "site": state.get("site") or LINKEDIN_SITE}
    if state.get("outcome") == "needs_confirmation":
        return {
            "apply_status": "manual",
            "review_reason": (
                state.get("review_reason")
                or "Candidate fact requires confirmation."
            ),
            "site": state.get("site") or LINKEDIN_SITE,
        }
    return {"site": state.get("site") or LINKEDIN_SITE}


def _live_fill_form(
    state: JobState,
    config: RunnableConfig | None,
    output_fn: Callable[[str], None],
) -> dict[str, Any]:
    thread_id = _config_thread_id(config)
    url = str(state.get("url") or "")
    profile_id = str(state.get("profile_id") or "")
    if not profile_id:
        profiles = load_work_profiles().get("work_profiles", {})
        profile_id = select_profile(state, profiles)
    resume = resolve_cv_path(state, load_profile(profile_id)["profile"])
    discovery = LinkedInDiscovery(output_fn=output_fn)
    context_manager = browser_session(headless=False)
    context = context_manager.__enter__()
    page = context.pages[0] if context.pages else context.new_page()
    hold = _BrowserHold(context_manager, page)
    try:
        discovery.ensure_login(page)
        discovery._navigate(page, url)
        discovery._raise_for_security_challenge(page)
        form = build_and_answer_form(page, state, cv_path=resume, output_fn=output_fn)
        _BROWSER_SESSIONS[thread_id] = hold
        return {
            "profile_id": profile_id,
            "form": form,
            "apply_status": state.get("apply_status"),
            "review_reason": state.get("review_reason"),
            "messages": [{"role": "system", "content": f"form:{form.step_label}"}],
        }
    except Exception as exc:
        hold.close()
        status, reason = classify_browser_error(exc)
        return {
            "apply_status": status,
            "review_reason": reason,
            "messages": [{"role": "system", "content": f"form_error:{reason}"}],
        }


def _live_submit(
    state: JobState,
    config: RunnableConfig | None,
    database_path: str | Path | None,
    output_fn: Callable[[str], None],
    dry_run: bool | None = None,
) -> dict[str, Any]:
    thread_id = _config_thread_id(config)
    hold = _BROWSER_SESSIONS.pop(thread_id, None)
    if hold is None:
        if state.get("apply_status"):
            return {}
        return {
            "apply_status": "manual",
            "review_reason": (
                "Browser session was lost after fill; Easy Apply must be reopened."
            ),
        }
    try:
        submit_application(
            hold.page,
            state,
            dry_run=dry_run,
            database_path=database_path,
            output_fn=output_fn,
        )
        return {
            "apply_status": state.get("apply_status"),
            "apply_url": state.get("apply_url"),
            "review_reason": state.get("review_reason"),
            "messages": [
                {"role": "system", "content": f"apply:{state.get('apply_status')}"}
            ],
        }
    finally:
        hold.close()


def _config_thread_id(config: RunnableConfig | None) -> str:
    if not config:
        return "linkedin:unknown"
    configurable = config.get("configurable") or {}
    return str(configurable.get("thread_id") or "linkedin:unknown")

