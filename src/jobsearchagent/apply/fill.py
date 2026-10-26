"""Apply a resolved FieldAnswer to a live Easy Apply control."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import FieldAnswer, FormField
from ..triage.easy_apply import field_looks_like_cv

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


_UPLOAD_RESUME = re.compile(
    r"upload (resume|cv|curriculum)|subir (cv|curr[ií]culum)|cargar (cv|curr[ií]culum)",
    re.IGNORECASE,
)


def apply_answer(
    page: Page,
    root: Locator,
    field: FormField,
    answer: FieldAnswer,
) -> bool:
    """Write ``answer`` into the matching control. Return whether a write ran."""

    if answer.value is None or answer.source == "deferred":
        return False

    value = answer.value
    if field.type == "file" or (
        field_looks_like_cv(field.label or field.id)
        and Path(str(value)).expanduser().is_file()
    ):
        return upload_resume(page, root, value)

    if field.type == "radio":
        return _select_radio(root, field, str(value))

    control = locate_control(root, field)
    if control is None:
        return False

    try:
        if field.type == "checkbox":
            control.set_checked(bool(value) and str(value).casefold() not in {"no", "false"})
            return True
        if field.type == "select":
            control.select_option(label=str(value))
            return True
        control.fill(str(value))
        return True
    except Exception:
        return False


def locate_control(root: Locator, field: FormField) -> Locator | None:
    """Find the input/select/textarea for ``field`` inside ``root``."""

    candidates = []
    if field.id:
        safe = field.id.replace("\\", "\\\\").replace('"', '\\"')
        candidates.extend(
            [
                root.locator(f'[id="{safe}"]'),
                root.locator(f'[name="{safe}"]'),
            ]
        )
    if field.label:
        candidates.append(root.get_by_label(field.label, exact=False))
    if field.type == "file":
        candidates.append(root.locator('input[type="file"]'))

    for locator in candidates:
        try:
            if locator.count():
                return locator.first
        except Exception:
            continue
    return None


def resume_upload_available(root: Locator) -> bool:
    """Whether this modal step has an Upload resume control or file input."""

    if _upload_resume_control(root) is not None:
        return True
    try:
        return root.locator('input[type="file"]').count() > 0
    except Exception:
        return False


def upload_resume(page: Page, root: Locator, cv_path: str | Path) -> bool:
    """Scroll to Upload resume, click it, and attach the local CV."""

    path = Path(cv_path).expanduser()
    if not path.is_file():
        return False
    if _already_uploaded(root, path):
        return True

    _scroll_modal_to_upload(root)
    button = _upload_resume_control(root)
    if button is not None:
        try:
            button.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            with page.expect_file_chooser(timeout=4_000) as chooser:
                button.click()
            chooser.value.set_files(str(path))
            return True
        except Exception:
            pass

    return _set_file_inputs(root, path)


def _upload_resume_control(root: Locator) -> Locator | None:
    candidates = [
        root.get_by_role("button", name=_UPLOAD_RESUME),
        root.locator("button, label, [role='button']").filter(has_text=_UPLOAD_RESUME),
        root.get_by_text(_UPLOAD_RESUME),
    ]
    for locator in candidates:
        try:
            if locator.count():
                return locator.first
        except Exception:
            continue
    return None


def _scroll_modal_to_upload(root: Locator) -> None:
    try:
        root.evaluate(
            """
            (root) => {
              const scrollers = [
                root.querySelector(".artdeco-modal__content"),
                root.querySelector(".jobs-easy-apply-modal__content"),
                root.querySelector(".jobs-easy-apply-content"),
                root
              ].filter(Boolean);
              for (const node of scrollers) {
                node.scrollTop = node.scrollHeight;
              }
              const upload = [...root.querySelectorAll("button, label, [role='button'], span, a")]
                .find((el) => /upload (resume|cv)/i.test((el.innerText || "").trim()));
              if (upload) upload.scrollIntoView({ block: "end", inline: "nearest" });
            }
            """
        )
    except Exception:
        pass


def _set_file_inputs(root: Locator, path: Path) -> bool:
    try:
        inputs = root.locator('input[type="file"]')
        if not inputs.count():
            return False
        inputs.last.set_input_files(str(path))
        return True
    except Exception:
        return False


def _already_uploaded(root: Locator, path: Path) -> bool:
    wanted = path.name.casefold()
    try:
        inputs = root.locator('input[type="file"]')
        for index in range(inputs.count()):
            name = inputs.nth(index).evaluate(
                "element => element.files && element.files[0] ? element.files[0].name : ''"
            )
            if str(name).casefold() == wanted:
                return True
    except Exception:
        return False
    return False


def _radio_scope(root: Locator, field: FormField) -> Locator:
    """Restrict Yes/No clicks to this question, not the whole modal."""

    if field.label:
        labelled = root.locator("fieldset", has_text=field.label)
        if labelled.count():
            return labelled.first
    if field.id:
        safe = field.id.replace("\\", "\\\\").replace('"', '\\"')
        named = root.locator(f'input[type="radio"][name="{safe}"]').first
        try:
            if named.count():
                group = named.locator("xpath=ancestor::fieldset[1]")
                if group.count():
                    return group.first
                wrap = named.locator(
                    "xpath=ancestor::*["
                    "@data-test-form-element "
                    "or contains(@class,'fb-dash-form-element') "
                    "or contains(@class,'jobs-easy-apply-form-element')"
                    "][1]"
                )
                if wrap.count():
                    return wrap.first
        except Exception:
            pass
    return root


def _select_radio(root: Locator, field: FormField, value: str) -> bool:
    try:
        clicked = root.evaluate(
            """
            (root, [question, value]) => {
              const needle = (question || "").replace(/\\s+/g, " ").trim()
                .slice(0, 48).toLowerCase();
              const want = (value || "").trim().toLowerCase();
              if (!needle || !want || /^(radio-group-)?_r_/.test(needle)) return false;
              const groups = [...root.querySelectorAll(
                "fieldset, .fb-dash-form-element, "
                + ".jobs-easy-apply-form-section__grouping, "
                + "[data-test-form-element], [role='radiogroup'], "
                + "[id^='radio-group-']"
              )];
              const titled = [...root.querySelectorAll("label, legend, p, span, div, h3")]
                .filter((node) => {
                  const text = (node.innerText || "").replace(/\\s+/g, " ").trim();
                  return text.toLowerCase().includes(needle) && text.length < 400;
                });
              for (const title of titled) {
                const neighbor = title.nextElementSibling || title.parentElement;
                if (neighbor && !groups.includes(neighbor)) groups.push(neighbor);
              }
              const matches = groups.filter((group) =>
                (group.innerText || "").toLowerCase().includes(needle)
                || titled.some((title) => title.nextElementSibling === group)
              );
              const group = matches.sort(
                (left, right) => left.innerText.length - right.innerText.length
              )[0];
              if (!group) return false;
              const options = [...group.querySelectorAll(
                "label, [role='radio'], input[type='radio'], "
                + "[data-test-text-selectable-option], "
                + ".fb-text-selectable__option, .fb-text-selectableoption"
              )];
              for (const option of options) {
                const siblingText = option.nextElementSibling
                  ? (option.nextElementSibling.innerText || "")
                  : "";
                const text = (
                  (option.innerText || "")
                  || option.getAttribute("data-test-text-selectable-option__input")
                  || option.getAttribute("value")
                  || siblingText
                  || ""
                ).replace(/\\s+/g, " ").trim().toLowerCase();
                if (text !== want && !text.startsWith(want + " ")) continue;
                const radio = option.matches("input[type='radio']")
                  ? option
                  : (option.querySelector("input[type='radio']")
                    || (option.previousElementSibling
                      && option.previousElementSibling.matches("input[type='radio']")
                      ? option.previousElementSibling
                      : null)
                    || option.parentElement
                      && option.parentElement.querySelector("input[type='radio']"));
                const target = radio || (option.matches("input, label, [role='radio']")
                  ? option
                  : (option.querySelector("label, input[type='radio']") || option));
                target.click();
                if (radio && radio.matches("input[type='radio']")) radio.checked = true;
                return true;
              }
              return false;
            }
            """,
            [field.label, value],
        )
        if clicked:
            return True
    except Exception:
        pass

    scope = _radio_scope(root, field)
    exact = re.compile(rf"^{re.escape(value)}$", re.IGNORECASE)
    candidates = [
        scope.get_by_role("radio", name=exact),
        scope.get_by_label(value, exact=True),
        scope.locator(
            f'input[type="radio"][value="{value.replace(chr(34), "")}"]'
        ),
        scope.get_by_text(exact),
    ]
    for locator in candidates:
        try:
            if not locator.count():
                continue
            option = locator.first
            option.scroll_into_view_if_needed()
            try:
                option.check()
            except Exception:
                option.click()
            return True
        except Exception:
            continue
    return False


def current_value(root: Locator, field: FormField) -> str | None:
    """Read the current string value of a control, if any."""

    if field.type == "radio":
        try:
            selected = root.evaluate(
                """
                (root, name) => {
                  if (!name) return "";
                  const radios = [...root.querySelectorAll("input[type='radio']")]
                    .filter((radio) => radio.name === name && radio.checked);
                  if (!radios.length) return "";
                  const radio = radios[0];
                  const value = (radio.getAttribute("value") || "").trim();
                  if (/^(yes|no|si|sí)$/i.test(value)) return value;
                  const parent = radio.parentElement;
                  const text = ((parent && parent.innerText) || "").trim();
                  const match = text.match(/\\b(Yes|No|Si|Sí)\\b/i);
                  return match ? match[1] : "";
                }
                """,
                field.id,
            )
            return str(selected) or None
        except Exception:
            return None

    control = locate_control(root, field)
    if control is None:
        return None
    try:
        if field.type == "file":
            name = control.evaluate(
                "element => element.files && element.files[0] ? element.files[0].name : ''"
            )
            return str(name) or None
        if field.type == "checkbox":
            return "true" if control.is_checked() else "false"
        value = str(control.input_value())
        if re.match(r"^(radio-group-)?_r_", value):
            return None
        return value
    except Exception:
        return None
