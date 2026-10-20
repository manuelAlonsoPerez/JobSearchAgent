"""Manual smoke runner for the artifact-requirements stage.

Two modes:

* **Persisted (default):** load the previous stage's ``JobState`` from SQLite and
  run the deterministic :func:`decide_requirements` policy.
* **Browser (``--browser``):** open the live LinkedIn Easy Apply modal, walk its
  steps without submitting, and report which artifacts the form actually asks
  for. A job whose Easy Apply flow only uploads a resume therefore needs just a
  tailored CV.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..browser import page_to_form, session
from ..database import get_job
from ..models import ArtifactType, Form, JobState
from ..utils.text import canonicalize_url
from .requirements import decide_requirements

if TYPE_CHECKING:
    from playwright.sync_api import Page


# Easy Apply modal navigation. Submit is listed so we can stop *before* it and
# never submit an application from a smoke test.
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

# The modal footer always exposes exactly one of these; the underlying job page
# never does, so a visible footer button is a reliable "modal is open" signal.
_FOOTER = re.compile(
    "|".join(pattern.pattern for pattern in (_REVIEW, _NEXT, _SUBMIT)),
    re.IGNORECASE,
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


def evaluate_persisted_job(
    identifier: str,
    *,
    database_path: str | Path | None = None,
    force_accepted_easy_apply: bool = False,
) -> dict[str, Any]:
    """Load the previous stage's state and evaluate its artifact requirements."""

    state = get_job(identifier, path=database_path)
    if state is None:
        raise LookupError(
            f"No persisted job matches {identifier!r}. Run LinkedIn discovery "
            "and triage first."
        )

    effective_state = JobState(**state)
    if force_accepted_easy_apply:
        effective_state["outcome"] = "accept"
        effective_state["easy_apply"] = True

    update = decide_requirements(effective_state)
    return {
        "mode": "persisted",
        "job": {
            "source_job_id": effective_state.get("source_job_id"),
            "title": effective_state.get("title"),
            "company": effective_state.get("company"),
            "url": effective_state.get("url"),
        },
        "previous_stage": {
            "profile_id": state.get("profile_id"),
            "outcome": state.get("outcome"),
            "fit_score": state.get("fit_score"),
            "easy_apply": state.get("easy_apply"),
        },
        "smoke_override_applied": force_accepted_easy_apply,
        **update,
    }


def inspect_live_requirements(
    identifier: str,
    *,
    database_path: str | Path | None = None,
    output_fn: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Open the live Easy Apply modal and report the artifacts it requires."""

    from ..discovery.linkedin import LinkedInDiscovery

    url = _resolve_job_url(identifier, database_path=database_path)
    discovery = LinkedInDiscovery(output_fn=output_fn)

    with session(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        discovery.ensure_login(page)
        discovery._navigate(page, url)
        discovery._raise_for_security_challenge(page)
        _open_easy_apply(page, output_fn)
        forms, texts = _walk_modal(page, output_fn)
        _dismiss_modal(page)

    detection = _detect_artifacts(forms, texts)
    return {
        "mode": "browser",
        "job": {"url": url},
        "steps": [
            {
                "step_label": form.step_label,
                "field_labels": [field.label for field in form.fields],
            }
            for form in forms
        ],
        **detection,
    }


def _resolve_job_url(
    identifier: str,
    *,
    database_path: str | Path | None,
) -> str:
    """Prefer the persisted canonical URL; fall back to a direct URL argument."""

    state = get_job(identifier, path=database_path)
    if state is not None and state.get("url"):
        return str(state["url"])
    if "://" in identifier:
        return canonicalize_url(identifier)
    raise LookupError(
        f"No persisted job matches {identifier!r} and it is not a URL. Run "
        "discovery first or pass the full LinkedIn job URL."
    )


# LinkedIn renders the Easy Apply modal as a visible role="dialog". We select it
# with the :visible pseudo-class (never a plain compound selector + state=visible,
# which locks onto the first, possibly-hidden, DOM match).
_MODAL_SELECTOR = '[role="dialog"]:visible'


def _easy_apply_button(page: Page):
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


def _open_easy_apply(page: Page, output_fn: Callable[[str], None]) -> None:
    button = _easy_apply_button(page)
    if button is None:
        raise RuntimeError(
            "This job has no visible Easy Apply button; it is likely an external "
            "application and is not inspected here."
        )

    output_fn("Opening the Easy Apply modal...")
    button.scroll_into_view_if_needed()
    button.click()

    # The modal is "open" once its footer (Next/Review/Submit) is visible. This
    # is more reliable than waiting on a container selector, which can lock onto
    # a hidden template dialog.
    try:
        page.get_by_role("button", name=_FOOTER).filter(
            visible=True
        ).first.wait_for(state="visible", timeout=20_000)
    except Exception as exc:
        _dump_open_failure(page, output_fn)
        raise RuntimeError(
            "Clicked Easy Apply but no modal footer (Next/Review/Submit) became "
            "visible within 20s. A diagnostic screenshot and the visible button "
            "names were logged above. LinkedIn may have shown a login/security "
            "wall, a localized label this runner does not recognize, or changed "
            "its modal markup."
        ) from exc


def _modal_root(page: Page):
    """Return the Easy Apply modal container.

    LinkedIn does not reliably expose ``role="dialog"`` on this modal, so we
    anchor on the footer button (which only exists inside the modal) and climb
    to its nearest modal/dialog ancestor. This scopes field and text extraction
    to the modal and keeps page-wide controls (global search, messaging, the
    premium sidebar) out of the results.
    """

    # Class-agnostic anchor: the modal's title heading ("Apply to <company>")
    # is unique to the Easy Apply modal. Climb to the nearest ancestor that also
    # contains a footer button; that ancestor is the modal box. This avoids
    # relying on LinkedIn's (changing) container class names, and keeps the
    # global nav, messaging, and premium-upsell panel out of the extraction.
    heading = page.get_by_role(
        "heading", name=re.compile(r"apply to\b", re.IGNORECASE)
    ).first
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

    # Fallbacks: a role=dialog / artdeco-modal container if present.
    footer = page.get_by_role("button", name=_FOOTER).filter(visible=True).first
    if footer.count():
        box = footer.locator(
            "xpath=ancestor::*["
            "@role='dialog'"
            " or contains(concat(' ', normalize-space(@class), ' '),"
            " ' artdeco-modal ')"
            "][1]"
        )
        if box.count():
            return box.first
    for selector in ('[role="dialog"]:visible', '.artdeco-modal:visible'):
        loc = page.locator(selector)
        if loc.count():
            return loc.last
    return page.locator("body")


def _walk_modal(
    page: Page,
    output_fn: Callable[[str], None],
) -> tuple[list[Form], list[str]]:
    """Walk Easy Apply steps without submitting, collecting each step's form."""

    forms: list[Form] = []
    texts: list[str] = []
    previous_text: str | None = None

    for step in range(_MAX_MODAL_STEPS):
        modal = _modal_root(page)
        text = _modal_text(page)
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

        if not _advance_modal(page, output_fn):
            output_fn(
                "Could not find an enabled Next/Review button; stopping. The "
                "visible footer button labels were logged above."
            )
            break

    return forms, texts


def _advance_modal(page: Page, output_fn: Callable[[str], None]) -> bool:
    """Click Review or Next (never Submit); return whether a control was used."""

    for pattern in (_REVIEW, _NEXT):
        button = page.get_by_role("button", name=pattern).filter(visible=True)
        for index in range(button.count()):
            control = button.nth(index)
            if control.is_enabled():
                control.scroll_into_view_if_needed()
                control.click()
                _wait_for_modal_advance(page)
                return True

    _log_footer_buttons(page, output_fn)
    return False


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


def _dismiss_modal(page: Page) -> None:
    """Best-effort close without submitting; discard any draft prompt."""

    try:
        close = page.get_by_role("button", name=re.compile(r"dismiss|close|cerrar", re.IGNORECASE))
        if close.count() and close.first.is_visible():
            close.first.click()
            page.wait_for_timeout(500)
        discard = page.get_by_role("button", name=_DISCARD)
        if discard.count() and discard.first.is_visible():
            discard.first.click()
    except Exception:
        # The browser context closes right after this; a stuck modal is harmless.
        pass


def _modal_text(page: Page) -> str:
    target = _modal_root(page)
    try:
        return target.inner_text(timeout=5_000)
    except Exception:
        return ""


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
            output_fn("Visible buttons on the page: " + ", ".join(sorted(set(names))))
    except Exception:
        pass

    try:
        shot = Path(tempfile.gettempdir()) / "smoke_requirements_open_failure.png"
        page.screenshot(path=str(shot), full_page=True)
        output_fn(f"Saved a diagnostic screenshot to {shot}")
    except Exception:
        pass


def _detect_artifacts(forms: list[Form], texts: list[str]) -> dict[str, Any]:
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


def _match_context(haystack: str, match: re.Match[str] | None) -> str | None:
    """Return a short snippet around a regex match to explain a signal."""

    if match is None:
        return None
    start = max(match.start() - 30, 0)
    end = min(match.end() + 30, len(haystack))
    snippet = haystack[start:end].replace("\n", " ").strip()
    return f"…{snippet}…"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manually test the artifact-requirements stage for one LinkedIn job."
        )
    )
    parser.add_argument(
        "job",
        help="LinkedIn job ID or full LinkedIn job URL.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override DB_PATH from .env.",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help=(
            "Open the live Easy Apply modal and detect the required artifacts "
            "from the actual form instead of the deterministic policy."
        ),
    )
    parser.add_argument(
        "--force-accepted-easy-apply",
        action="store_true",
        help=(
            "Persisted mode only: smoke-test the accepted Easy Apply branch "
            "without changing the stored triage result."
        ),
    )
    arguments = parser.parse_args()

    try:
        if arguments.browser:
            result = inspect_live_requirements(
                arguments.job,
                database_path=arguments.db_path,
            )
        else:
            result = evaluate_persisted_job(
                arguments.job,
                database_path=arguments.db_path,
                force_accepted_easy_apply=arguments.force_accepted_easy_apply,
            )
    except (LookupError, ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
