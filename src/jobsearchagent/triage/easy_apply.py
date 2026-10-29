"""Open and navigate the live LinkedIn Easy Apply modal.

This module contains the reusable browser mechanics for the Easy Apply flow:
opening the modal, walking its steps *without ever submitting*, scoping field
and text extraction to the modal, and mapping what it finds to required
artifacts. It operates on an already-authenticated, already-navigated
Playwright ``Page`` so callers control session, login, and navigation.

Both the manual smoke runner (``smoke_requirements``) and the requirements
decision (``requirements``) build on these helpers.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..browser import page_to_form
from ..models import ArtifactType, Form

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


# Easy Apply modal navigation. Submit is listed so we can stop *before* it and
# never submit an application.
_EASY_APPLY = re.compile(
    r"easy apply|solicitud sencilla|enkel søknad|candidatura simplificada",
    re.IGNORECASE,
)
_REVIEW = re.compile(
    r"review|revisar|gjennomgå|se gjennom",
    re.IGNORECASE,
)
_NEXT = re.compile(
    r"continue|next|siguiente|continuar|neste|fortsett",
    re.IGNORECASE,
)
_SUBMIT = re.compile(
    r"submit application|submit|enviar solicitud|send søknad|send inn",
    re.IGNORECASE,
)
_DISCARD = re.compile(r"discard|descartar|forkast", re.IGNORECASE)
_SAVE_JOB = re.compile(
    r"save( this)?( job)?$|saved$|unsave|"
    r"lagre|lagret|"
    r"guardar( oferta)?$|guardado",
    re.IGNORECASE,
)
_ALREADY_SAVED = re.compile(r"saved|lagret|guardado|unsave", re.IGNORECASE)
_APPLY_HEADING = re.compile(r"^apply to\b", re.IGNORECASE)
_SAFETY_REMINDER = re.compile(
    r"job search safety reminder|"
    r"safety reminder|"
    r"sikkerhetspåminnelse|"
    r"jobbsøkersikkerhet",
    re.IGNORECASE,
)
_CONTINUE_APPLYING = re.compile(
    r"continue applying|"
    r"fortsett å søke|"
    r"fortsett med å søke|"
    r"fortsett med søknaden|"
    r"continuar con la solicitud",
    re.IGNORECASE,
)

# Footer labels used *inside* the Easy Apply modal. Matching these on the
# whole LinkedIn page is not a signal the modal is open: the header, feed,
# and carousels also expose Next / Submit.
_FOOTER = re.compile(
    "|".join(pattern.pattern for pattern in (_REVIEW, _NEXT, _SUBMIT)),
    re.IGNORECASE,
)
_APPLY_SHELL = (
    ".jobs-easy-apply-modal:visible, "
    ".jobs-easy-apply-content:visible, "
    "[class*='jobs-easy-apply-modal']:visible"
)

# Artifact signals detected from field labels and modal text.
_CV_KEYWORDS = re.compile(
    r"\b(resume|résumé|cv|curriculum|currículum|curriculo|currículo|lebenslauf)\b",
    re.IGNORECASE,
)
_COVER_LETTER_KEYWORDS = re.compile(
    r"cover letter|carta de presentaci|motivation letter|motivasjonsbrev"
    r"|søknadsbrev",
    re.IGNORECASE,
)

_MAX_MODAL_STEPS = 6

_OPEN_FAILURE_SCREENSHOT = "easy_apply_open_failure.png"


def inspect_modal(
    page: Page,
    output_fn: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Open, walk, and dismiss the Easy Apply modal; return detected artifacts.

    The ``page`` must already be authenticated and on the job's LinkedIn page.
    Returns the per-step field summary plus :func:`detect_artifacts` output.
    """

    open_easy_apply(page, output_fn)
    forms, texts = walk_modal(page, output_fn)
    dismiss_modal(page)

    detection = detect_artifacts(forms, texts)
    return {
        "steps": [
            {
                "step_label": form.step_label,
                "field_labels": [field.label for field in form.fields],
            }
            for form in forms
        ],
        **detection,
    }


def has_easy_apply(page: Page) -> bool:
    """Whether a visible Easy Apply button exists on the current job page."""

    return _easy_apply_button(page) is not None


def reached_submit_step(page: Page) -> bool:
    """Whether the modal footer is the Submit control — stop before clicking it."""

    if not easy_apply_modal_open(page):
        return False
    return (
        modal_root(page)
        .get_by_role("button", name=_SUBMIT)
        .filter(visible=True)
        .count()
        > 0
    )


def easy_apply_modal_open(page: Page) -> bool:
    """Whether the Easy Apply dialog itself is on screen.

    Page chrome (search, language, notifications) and stray Next/Submit
    buttons do not count.
    """

    try:
        if _apply_heading(page).count():
            return True
        return page.locator(_APPLY_SHELL).count() > 0
    except Exception:
        return False


_SAVED_RESUME = re.compile(
    r"\b(pdf|docx?)\b.*\.(pdf|docx?)\b"
    r"|\.(pdf|docx?)\b.*\d{1,2}/\d{1,2}/\d{2,4}",
    re.IGNORECASE,
)


def field_looks_like_saved_resume(label: str) -> bool:
    """Whether a label is a previously uploaded LinkedIn resume option."""

    return bool(_SAVED_RESUME.search(label or ""))


def field_looks_like_cv(label: str) -> bool:
    """Whether a field label is a resume / CV upload or saved-resume option."""

    return bool(_CV_KEYWORDS.search(label or "")) or field_looks_like_saved_resume(
        label
    )


def field_looks_like_cover_letter(label: str) -> bool:
    """Whether a field label is a cover-letter upload or text box."""

    return bool(_COVER_LETTER_KEYWORDS.search(label or ""))


def open_easy_apply(page: Page, output_fn: Callable[[str], None] = print) -> None:
    """Click the Easy Apply button and wait for the modal footer to appear."""

    button = _easy_apply_button(page)
    if button is None:
        raise RuntimeError(
            "This job has no visible Easy Apply button; it is likely an external "
            "application and is not inspected here."
        )

    output_fn("Opening the Easy Apply modal...")
    button.scroll_into_view_if_needed()
    button.click()
    _dismiss_job_safety_reminder(page, output_fn)

    try:
        _apply_heading(page).or_(page.locator(_APPLY_SHELL)).first.wait_for(
            state="visible",
            timeout=20_000,
        )
    except Exception as exc:
        _dump_open_failure(page, output_fn)
        raise RuntimeError(
            "Clicked Easy Apply but the Apply-to modal did not become visible "
            "within 20s. A diagnostic screenshot and the visible button names "
            "were logged above. LinkedIn may have shown a login/security wall "
            "or changed its modal markup."
        ) from exc


def walk_modal(
    page: Page,
    output_fn: Callable[[str], None] = print,
) -> tuple[list[Form], list[str]]:
    """Walk Easy Apply steps without submitting, collecting each step's form."""

    forms: list[Form] = []
    texts: list[str] = []
    previous_text: str | None = None

    for _ in range(_MAX_MODAL_STEPS):
        modal = modal_root(page)
        text = modal_text(page)
        if text == previous_text:
            break  # A click did not advance the modal; stop defensively.
        form = page_to_form(page, root=modal)
        forms.append(form)
        texts.append(text)
        previous_text = text
        output_fn(
            f"Step {len(forms)}: {form.step_label or 'unnamed'} "
            f"({len(form.fields)} fields)"
        )

        # Submit/Next/Review live at page level (there is no collision: the job
        # page itself has no such buttons), which is robust to the modal lacking
        # role="dialog".
        submit = page.get_by_role("button", name=_SUBMIT).filter(visible=True)
        if submit.count():
            output_fn("Reached the review step; stopping before submission.")
            break

        if not advance_modal(page, output_fn):
            output_fn(
                "Could not find an enabled Next/Review button; stopping. The "
                "visible footer button labels were logged above."
            )
            break

    return forms, texts


def modal_root(page: Page) -> Locator:
    """Return the Easy Apply modal container.

    LinkedIn does not reliably expose ``role="dialog"`` on this modal, so we
    anchor on the modal's title heading ("Apply to <company>") and climb to the
    nearest ancestor that also contains a footer button. Never fall back to
    ``body`` or a random ``artdeco-modal``: that scrapes page chrome
    (search, language, notifications) instead of the application.
    """

    heading = _apply_heading(page).first
    if heading.count():
        box = heading.locator(
            "xpath=ancestor::*[.//button["
            "contains(normalize-space(.), 'Next')"
            " or contains(normalize-space(.), 'Review')"
            " or contains(normalize-space(.), 'Submit')"
            " or contains(normalize-space(.), 'Continue')"
            "]][1]"
        )
        if box.count():
            return box.first
        shell = heading.locator(
            "xpath=ancestor::*["
            "@role='dialog'"
            " or contains(@class,'artdeco-modal')"
            " or contains(@class,'jobs-easy-apply')"
            "][1]"
        )
        if shell.count():
            return shell.first

    shell = page.locator(_APPLY_SHELL)
    if shell.count():
        return shell.last
    return page.locator(".jobs-easy-apply-modal:visible")


def advance_modal(page: Page, output_fn: Callable[[str], None] = print) -> bool:
    """Click Review or Next (never Submit); return whether a control was used."""

    if not easy_apply_modal_open(page):
        _log_footer_buttons(page, output_fn)
        return False

    root = modal_root(page)
    for pattern in (_REVIEW, _NEXT):
        button = root.get_by_role("button", name=pattern).filter(visible=True)
        for index in range(button.count()):
            control = button.nth(index)
            if control.is_enabled():
                control.scroll_into_view_if_needed()
                control.click()
                _wait_for_modal_advance(page)
                return True

    _log_footer_buttons(page, output_fn)
    return False


def dismiss_modal(page: Page) -> None:
    """Best-effort close without submitting; discard any draft prompt."""

    try:
        close = page.get_by_role(
            "button", name=re.compile(r"dismiss|close|cerrar", re.IGNORECASE)
        )
        if close.count() and close.first.is_visible():
            close.first.click()
            page.wait_for_timeout(500)
        discard = page.get_by_role("button", name=_DISCARD)
        if discard.count() and discard.first.is_visible():
            discard.first.click()
            page.wait_for_timeout(400)
    except Exception:
        # The browser context often closes right after this; a stuck modal is
        # harmless.
        pass


def save_job(page: Page, output_fn: Callable[[str], None] = print) -> bool:
    """Click LinkedIn's job-page Save control. Never touches Submit."""

    if easy_apply_modal_open(page):
        output_fn("Easy Apply modal is still open; not clicking Save yet.")
        return False

    candidates = [
        page.locator("button.jobs-save-button"),
        page.get_by_role("button", name=_SAVE_JOB),
        page.locator('[aria-label*="Save" i]').filter(has_not_text=_EASY_APPLY),
    ]
    for locator in candidates:
        try:
            count = locator.count()
        except Exception:
            continue
        for index in range(count):
            control = locator.nth(index)
            try:
                if not control.is_visible() or not control.is_enabled():
                    continue
                label = (
                    control.get_attribute("aria-label")
                    or control.inner_text(timeout=1_000)
                    or ""
                ).strip()
            except Exception:
                continue
            if _ALREADY_SAVED.search(label) and "unsave" not in label.casefold():
                output_fn(f"Job already saved ({label}).")
                return True
            control.click()
            output_fn("Clicked Save on the job posting.")
            return True
    output_fn("No Save button found on the job page.")
    return False


def modal_text(page: Page) -> str:
    """Return the visible text scoped to the Easy Apply modal."""

    target = modal_root(page)
    try:
        return target.inner_text(timeout=5_000)
    except Exception:
        return ""


def detect_artifacts(forms: list[Form], texts: list[str]) -> dict[str, Any]:
    """Map the collected Easy Apply fields and text to required artifacts."""

    labels = [field.label for form in forms for field in form.fields]
    has_file_field = any(
        field.type == "file" for form in forms for field in form.fields
    )
    haystack = "\n".join([*labels, *texts])

    cv_match = _CV_KEYWORDS.search(haystack)
    cover_letter_match = _COVER_LETTER_KEYWORDS.search(haystack)
    cv_signal = bool(cv_match) or has_file_field
    cover_letter_signal = bool(cover_letter_match)

    required: list[ArtifactType] = []
    if cv_signal:
        required.append("cv")
    if cover_letter_signal:
        required.append("cover_letter")

    return {
        "required_artifacts": required,
        "signals": {
            "cv_field_or_keyword": cv_signal,
            "cover_letter_field_or_keyword": cover_letter_signal,
            "has_file_upload_field": has_file_field,
        },
        "signal_sources": {
            "cv_keyword": _match_context(haystack, cv_match),
            "cover_letter_keyword": _match_context(haystack, cover_letter_match),
        },
    }


def _apply_heading(page: Page) -> Locator:
    """Visible 'Apply to …' title of the Easy Apply dialog."""

    return page.get_by_role("heading", name=_APPLY_HEADING).filter(visible=True)


def _dismiss_job_safety_reminder(
    page: Page,
    output_fn: Callable[[str], None],
) -> bool:
    """Click through LinkedIn's job-search safety interstitial if it appears.

    Some Easy Apply jobs show this reminder instead of the Apply-to modal.
    The apply form only opens after ``Continue applying``.
    """

    try:
        page.wait_for_timeout(400)
    except Exception:
        pass

    heading = page.get_by_role("heading", name=_SAFETY_REMINDER).filter(visible=True)
    continue_button = page.get_by_role("button", name=_CONTINUE_APPLYING).filter(
        visible=True
    )
    if not heading.count() and not continue_button.count():
        return False

    output_fn("LinkedIn job-search safety reminder; clicking Continue applying.")
    if continue_button.count():
        continue_button.first.click()
        return True
    return False


def _easy_apply_button(page: Page) -> Locator | None:
    """Return the visible Easy Apply control, preferring LinkedIn's class hook."""

    candidates = [
        page.locator("button.jobs-apply-button").filter(has_text=_EASY_APPLY),
        page.locator("button.jobs-apply-button"),
        page.get_by_role("button", name=_EASY_APPLY),
    ]
    for locator in candidates:
        for index in range(locator.count()):
            control = locator.nth(index)
            if control.is_visible():
                return control
    return None


def _wait_for_modal_advance(page: Page) -> None:
    """Give the next step time to render before re-reading the modal."""

    try:
        page.wait_for_timeout(400)
        page.wait_for_load_state("networkidle", timeout=4_000)
    except Exception:
        page.wait_for_timeout(1_200)


def _log_footer_buttons(page: Page, output_fn: Callable[[str], None]) -> None:
    buttons = page.get_by_role("button", name=_FOOTER)
    labels: list[str] = []
    try:
        for index in range(min(buttons.count(), 20)):
            control = buttons.nth(index)
            if control.is_visible():
                text = (control.inner_text(timeout=1_000) or "").strip()
                state = "enabled" if control.is_enabled() else "disabled"
                if text:
                    labels.append(f"{text.replace(chr(10), ' ')} [{state}]")
    except Exception:
        pass
    if labels:
        output_fn("Footer buttons: " + "; ".join(labels))


def _dump_open_failure(page: Page, output_fn: Callable[[str], None]) -> None:
    """Log the visible buttons and a screenshot to help diagnose a stuck open."""

    try:
        names: list[str] = []
        buttons = page.get_by_role("button")
        for index in range(min(buttons.count(), 40)):
            control = buttons.nth(index)
            if control.is_visible():
                label = (control.inner_text(timeout=1_000) or "").strip()
                if label:
                    names.append(label.replace("\n", " "))
        if names:
            output_fn(
                "Visible buttons on the page: " + ", ".join(sorted(set(names)))
            )
    except Exception:
        pass

    try:
        shot = Path(tempfile.gettempdir()) / _OPEN_FAILURE_SCREENSHOT
        page.screenshot(path=str(shot), full_page=True)
        output_fn(f"Saved a diagnostic screenshot to {shot}")
    except Exception:
        pass


def _match_context(haystack: str, match: re.Match[str] | None) -> str | None:
    """Return a short snippet around a regex match to explain a signal."""

    if match is None:
        return None
    start = max(match.start() - 30, 0)
    end = min(match.end() + 30, len(haystack))
    snippet = haystack[start:end].replace("\n", " ").strip()
    return f"…{snippet}…"
