"""Walk a live Easy Apply modal and answer every supported field.

Submission is intentionally out of scope: this module stops when the footer
becomes Submit. Cover letters and tailored CVs are skipped for this pass.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..browser import page_to_form
from ..config import get_settings, load_work_profiles
from ..jev import Jev
from ..llm import chat
from ..models import FieldAnswer, Form, FormField, JobState
from ..triage.easy_apply import (
    _MAX_MODAL_STEPS,
    _log_footer_buttons,
    advance_modal,
    easy_apply_modal_open,
    field_looks_like_cover_letter,
    field_looks_like_cv,
    field_looks_like_saved_resume,
    modal_root,
    modal_text,
    open_easy_apply,
    reached_submit_step,
)
from ..triage.triage import select_profile
from ..utils.profile import load_profile, profile_facts
from .answers import (
    has_related_facts,
    must_defer,
    resolve_cover_letter,
    resolve_cv_path,
    resolve_field,
)
from .fill import apply_answer, current_value, resume_upload_available, upload_resume

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


ChatFn = Callable[[Sequence[Mapping[str, Any]]], str]


def log_form_answers(
    form: Form,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Print every modal answer so a dry-run can be debugged in the terminal."""

    output_fn(f"Easy Apply answers ({len(form.answers)}):")
    if not form.answers:
        output_fn("  (none)")
        return
    labels = {field.id: field.label or field.id for field in form.fields}
    for answer in form.answers:
        label = labels.get(answer.field_id, answer.field_id)
        value = answer.value
        shown = (
            str(value)[:77] + "..."
            if isinstance(value, str) and len(value) > 80
            else value
        )
        output_fn(f"  {label} = {shown} [{answer.source}]")


def _modal_snippet(page: Page, limit: int = 220) -> str:
    try:
        text = re.sub(r"\s+", " ", modal_text(page) or "").strip()
    except Exception:
        return ""
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _usable_existing(field: FormField, existing: str | None) -> bool:
    """Whether a pre-filled value is a real answer, not a generated widget id."""

    if not existing or field.type == "file":
        return False
    text = str(existing).strip()
    if re.match(r"^(radio-group-)?_r_[a-z0-9]+_?$", text, re.IGNORECASE):
        return False
    if field.type == "radio":
        return bool(re.match(r"^(yes|no|si|sí)$", text, re.IGNORECASE))
    return True


def build_and_answer_form(
    page: Page,
    state: JobState,
    *,
    jev: Jev | None = None,
    chat_fn: ChatFn | None = None,
    cv_path: str | Path | None = None,
    profile_config: Mapping[str, Any] | None = None,
    output_fn: Callable[[str], None] = print,
) -> Form:
    """Open Easy Apply if needed, fill known fields, and stop at Review."""

    config = profile_config or load_work_profiles()
    profile_id = str(state.get("profile_id") or "")
    if not profile_id:
        profile_id = select_profile(state, config.get("work_profiles", {}))
        state["profile_id"] = profile_id

    context = load_profile(profile_id, config)
    profile = context["profile"]
    facts = profile_facts(profile_id, config)
    resume = resolve_cv_path(state, profile, override=cv_path)
    letter = resolve_cover_letter(state)
    settings = get_settings()
    history: list[dict[str, Any]] = [
        dict(message) if isinstance(message, Mapping) else {"role": "user", "content": str(message)}
        for message in state.get("messages") or []
    ]

    if not easy_apply_modal_open(page):
        open_easy_apply(page, output_fn)
    if not easy_apply_modal_open(page):
        raise RuntimeError(
            "Easy Apply modal did not open; refusing to fill page chrome."
        )

    all_fields: list[FormField] = []
    all_answers: list[FieldAnswer] = []
    last_label: str | None = None
    previous_text = ""
    deferred_required: list[str] = []

    for step_index in range(_MAX_MODAL_STEPS):
        if reached_submit_step(page):
            output_fn("Reached the review step; stopping before submission.")
            break

        modal = modal_root(page)
        step = page_to_form(
            page,
            url=state.get("url") or page.url,
            root=modal,
        )
        last_label = step.step_label or last_label
        output_fn(
            f"Step {step_index + 1}: {step.step_label or 'unnamed'} "
            f"({len(step.fields)} fields)"
        )
        snippet = _modal_snippet(page)
        if snippet:
            output_fn(f"  modal: {snippet}")
        _log_footer_buttons(page, output_fn)
        for field in step.fields:
            output_fn(
                f"  saw {field.type} {field.label!r} "
                f"required={field.required} options={field.options}"
            )

        step_answers = _answer_step(
            page,
            modal,
            step.fields,
            profile_id=profile_id,
            profile_config=config,
            context=context,
            facts=facts,
            cv_path=resume,
            cover_letter=letter,
            job_location=str(state.get("location") or ""),
            jev=jev,
            chat_fn=chat_fn,
            history=history,
            output_fn=output_fn,
        )
        all_fields.extend(step.fields)
        all_answers.extend(step_answers)
        for field, answer in zip(step.fields, step_answers):
            if field.required and answer.source == "deferred":
                deferred_required.append(field.label or field.id)

        if reached_submit_step(page):
            output_fn("Reached the review step; stopping before submission.")
            break

        advanced = False
        for attempt in range(max(1, settings.max_browser_attempts)):
            if advance_modal(page, output_fn):
                advanced = True
                break
            output_fn(
                f"Could not advance (attempt {attempt + 1}/"
                f"{settings.max_browser_attempts})."
            )
        text = modal.inner_text(timeout=5_000) if modal else ""
        if not advanced or text == previous_text:
            if deferred_required:
                output_fn(
                    "Stopping: required fields are still unanswered: "
                    + ", ".join(deferred_required)
                )
            else:
                output_fn("Could not advance the modal; stopping before submit.")
            break
        previous_text = text

    form = Form(
        url=str(state.get("url") or page.url),
        step_label=last_label,
        fields=all_fields,
        answers=all_answers,
    )
    state["form"] = form
    state["messages"] = history
    if deferred_required:
        state["review_reason"] = (
            "Easy Apply needs review: "
            + ", ".join(dict.fromkeys(deferred_required))
        )
        state["apply_status"] = "manual"
    elif reached_submit_step(page):
        state["apply_status"] = "dry_run_ready"
    log_form_answers(form, output_fn)
    return form


def _answer_step(
    page: Page,
    modal: Locator,
    fields: list[FormField],
    *,
    profile_id: str,
    profile_config: Mapping[str, Any],
    context: Mapping[str, Any],
    facts: list[str],
    cv_path: Path | None,
    cover_letter: str | Path | None,
    job_location: str,
    jev: Jev | None,
    chat_fn: ChatFn | None,
    history: list[dict[str, Any]],
    output_fn: Callable[[str], None],
) -> list[FieldAnswer]:
    answers: list[FieldAnswer] = []
    uploaded = False
    if cv_path is not None and resume_upload_available(modal):
        uploaded = upload_resume(page, modal, cv_path)
        output_fn(
            f"  profile CV {'uploaded' if uploaded else 'not uploaded'}: {cv_path}"
        )

    for field in fields:
        if uploaded and not field_looks_like_cover_letter(field.label or field.id) and (
            field.type == "file"
            or field_looks_like_cv(field.label or field.id)
            or field_looks_like_saved_resume(field.label or field.id)
        ):
            answers.append(
                FieldAnswer(
                    field_id=field.id,
                    value=str(cv_path),
                    source="profile",
                )
            )
            output_fn(f"  profile {field.label!r} uploaded")
            continue
        existing = current_value(modal, field)
        answer = resolve_field(
            field,
            profile_id=profile_id,
            profile_config=profile_config,
            cv_path=cv_path,
            cover_letter=cover_letter,
            facts=facts,
            context=context,
            job_location=job_location,
        )
        if (
            answer.value is None
            and not must_defer(field)
            and has_related_facts(field.label or field.id, facts)
        ):
            answer = _model_answer(
                field,
                profile_id=profile_id,
                facts=facts,
                jev=jev,
                chat_fn=chat_fn,
                history=history,
            )
        if (
            answer.value is None
            and _usable_existing(field, existing)
        ):
            # LinkedIn often pre-fills contact info from the logged-in account.
            answer = FieldAnswer(
                field_id=field.id,
                value=existing,
                source="profile",
            )
            output_fn(f"  keep {field.label!r} (already filled)")
        elif answer.value is not None:
            wrote = apply_answer(page, modal, field, answer)
            output_fn(
                f"  {answer.source} {field.label!r} "
                f"{'written' if wrote else 'resolved, not written'}"
            )
        else:
            output_fn(f"  deferred {field.label!r}")
        answers.append(answer)
    return answers


def _model_answer(
    field: FormField,
    *,
    profile_id: str,
    facts: list[str],
    jev: Jev | None,
    chat_fn: ChatFn | None,
    history: list[dict[str, Any]],
) -> FieldAnswer:
    """Ask JEV (bounded) or GEN (free text) only when the profile can ground it."""

    if field.options or field.type in {"select", "radio"}:
        return _jev_answer(field, facts=facts, jev=jev)
    if field.type in {"text", "textarea", "number"}:
        return _gen_answer(
            field,
            profile_id=profile_id,
            facts=facts,
            chat_fn=chat_fn,
            history=history,
        )
    return FieldAnswer(field_id=field.id, value=None, source="deferred")


def _jev_answer(
    field: FormField,
    *,
    facts: list[str],
    jev: Jev | None,
) -> FieldAnswer:
    options = list(field.options)
    if len(options) < 2:
        return FieldAnswer(field_id=field.id, value=None, source="deferred")
    client = jev or Jev()
    owns = jev is None
    try:
        choice, confidence = client.choice(
            {
                "field": field.label,
                "facts": facts[:80],
            },
            (
                "Select the option that is explicitly grounded in the profile "
                "facts. If none is grounded, you must not guess."
            ),
            {option: option for option in options},
        )
    except Exception:
        return FieldAnswer(field_id=field.id, value=None, source="deferred")
    finally:
        if owns:
            client.close()
    if choice not in options:
        return FieldAnswer(field_id=field.id, value=None, source="deferred")
    if not has_related_facts(str(choice), facts) and not has_related_facts(
        field.label or "",
        facts,
    ):
        return FieldAnswer(field_id=field.id, value=None, source="deferred")
    return FieldAnswer(
        field_id=field.id,
        value=choice,
        source="jev",
        confidence=confidence,
    )


def _gen_answer(
    field: FormField,
    *,
    profile_id: str,
    facts: list[str],
    chat_fn: ChatFn | None,
    history: list[dict[str, Any]],
) -> FieldAnswer:
    generate = chat_fn or (lambda messages: chat(messages, model="apply"))
    if not history:
        history.append(
            {
                "role": "system",
                "content": (
                    "Answer Easy Apply questions using only the candidate "
                    f"profile '{profile_id}'. Never invent facts. If the "
                    "profile does not support an answer, reply with exactly "
                    "DEFER."
                ),
            }
        )
        history.append(
            {
                "role": "user",
                "content": "Profile facts:\n" + "\n".join(facts[:120]),
            }
        )
    history.append(
        {
            "role": "user",
            "content": (
                f"Field '{field.label}' ({field.type}). "
                "Reply with the answer text only, or DEFER."
            ),
        }
    )
    try:
        text = generate(history).strip()
    except Exception:
        history.append({"role": "assistant", "content": "DEFER"})
        return FieldAnswer(field_id=field.id, value=None, source="deferred")
    history.append({"role": "assistant", "content": text})
    if not text or text.casefold() == "defer":
        return FieldAnswer(field_id=field.id, value=None, source="deferred")
    if not has_related_facts(text, facts) and not has_related_facts(
        field.label or "",
        facts,
    ):
        return FieldAnswer(field_id=field.id, value=None, source="deferred")
    return FieldAnswer(field_id=field.id, value=text, source="llm")
