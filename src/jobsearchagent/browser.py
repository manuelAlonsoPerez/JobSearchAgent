"""Persistent Playwright browser session and form serialization helpers."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import get_settings
from .models import Form, FormField, FormFieldType

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page


_SESSION_LOCK = threading.Lock()
_SUPPORTED_INPUT_TYPES: set[str] = {
    "text",
    "email",
    "number",
    "file",
    "checkbox",
    "radio",
}


@contextmanager
def session(
    *,
    profile_dir: Path | None = None,
    headless: bool = False,
) -> Iterator[BrowserContext]:
    """Launch one persistent Chromium context.

    Login is intentionally manual. Reusing the configured profile preserves
    the authenticated LinkedIn session between runs.
    """

    from playwright.sync_api import sync_playwright

    settings = get_settings()
    user_data_dir = profile_dir or settings.chrome_profile_dir
    user_data_dir = Path(user_data_dir).expanduser()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    with _SESSION_LOCK:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=headless,
                slow_mo=settings.linkedin_action_delay_ms,
                viewport={"width": 1440, "height": 1000},
            )
            try:
                yield context
            finally:
                context.close()


def a11y_text(page: Page) -> str:
    """Serialize the page's accessible structure, with visible text fallback."""

    body = page.locator("body")
    try:
        snapshot = body.aria_snapshot(timeout=10_000)
        if snapshot.strip():
            return snapshot
    except Exception:
        # Older Playwright/browser combinations may not support aria_snapshot.
        pass
    return body.inner_text(timeout=10_000)


def page_to_form(page: Page, url: str | None = None, root: Any = None) -> Form:
    """Normalize form controls on the page, or within an explicit ``root``.

    ``root`` may be a Playwright ``Locator`` scoping extraction to one container
    (e.g. a modal). When omitted, the visible dialog/form is auto-detected.
    """

    if root is not None:
        pass
    else:
        dialog = page.locator('[role="dialog"]:visible').last
        if dialog.count():
            root = dialog
        else:
            visible_form = page.locator("form:visible").last
            root = visible_form if visible_form.count() else page.locator("body")

    raw_fields: list[dict[str, Any]] = root.evaluate(
        """
        root => {
          const controls = [...root.querySelectorAll("input, textarea, select")];

          const visibleText = (element) => {
            if (!element) return "";
            return (element.innerText || "")
              .replace(/\\s+/g, " ")
              .replace(/\\*\\s*$/, "")
              .trim();
          };

          const isGenerated = (text) =>
            /^(radio-group-)?_r_[a-z0-9]+_?$/i.test((text || "").trim())
            || /^urn:li:/i.test(text || "");

          const isOptionText = (text) => /^(yes|no|si|sí)$/i.test(text || "");

          const isDocumentOption = (text) => {
            const value = (text || "").replace(/\\s+/g, " ").trim();
            return /^(pdf|docx?|cv|resume)\\b/i.test(value)
              && /\\.(pdf|docx?)\\b/i.test(value);
          };

          const isQuestionText = (text) => {
            const value = (text || "").replace(/this field is required/ig, "").trim();
            return Boolean(
              value
              && value.length > 8
              && !isGenerated(value)
              && !isOptionText(value)
              && !isDocumentOption(value)
            );
          };

          const labelFor = (element) => {
            const labels = element.labels ? [...element.labels] : [];
            const labelText = labels.map(label => label.innerText.trim())
              .filter((text) => text && !isGenerated(text)).join(" ");
            if (labelText) return labelText;
            const aria = (element.getAttribute("aria-label") || "").trim();
            if (aria && !isGenerated(aria)) return aria;
            const placeholder = (element.getAttribute("placeholder") || "").trim();
            if (placeholder && !isGenerated(placeholder)) return placeholder;
            return "";
          };

          const optionText = (element) => {
            const direct = labelFor(element);
            if (direct && (isOptionText(direct) || !isGenerated(direct))) {
              const match = direct.match(/\\b(Yes|No|Si|Sí)\\b/i);
              return match ? match[1] : direct;
            }
            const value = (element.getAttribute("value") || "").trim();
            if (value && !isGenerated(value)) return value;
            let sibling = element.nextElementSibling;
            while (sibling) {
              const text = visibleText(sibling);
              const match = text.match(/^(Yes|No|Si|Sí)\\b/i);
              if (match) return match[1];
              sibling = sibling.nextElementSibling;
            }
            const parentText = visibleText(element.parentElement);
            const match = parentText.match(/\\b(Yes|No|Si|Sí)\\b/i);
            return match ? match[1] : "";
          };

          const labelledByText = (element) => {
            if (!element) return "";
            return (element.getAttribute("aria-labelledby") || "")
              .split(/\\s+/)
              .map((id) => visibleText(document.getElementById(id)))
              .filter(isQuestionText)
              .join(" ");
          };

          const previousQuestion = (element) => {
            let node = element;
            for (let depth = 0; depth < 8 && node; depth += 1) {
              const named = labelledByText(node);
              if (named) return named;
              let previous = node.previousElementSibling;
              while (previous) {
                const text = visibleText(previous).replace(
                  /this field is required/ig,
                  ""
                ).trim();
                if (isQuestionText(text)) return text;
                previous = previous.previousElementSibling;
              }
              node = node.parentElement;
            }
            return "";
          };

          const titleSelector = [
            "legend",
            ".fb-dash-form-element__label",
            "[data-test-form-element-label]",
            "[data-test-form-builder-radio-button-form-component__title]",
            "label.fb-dash-form-element__label",
          ].join(", ");

          const questionLabel = (element) => {
            const named = labelledByText(element);
            if (named) return named;
            const hosts = [
              element.closest("[role='radiogroup']"),
              element.closest("fieldset"),
              element.closest(
                "[data-test-form-element], .fb-dash-form-element, "
                + ".jobs-easy-apply-form-element, "
                + ".jobs-easy-apply-form-section__grouping, "
                + "[id^='radio-group-']"
              ),
            ].filter(Boolean);
            for (const host of hosts) {
              const hostNamed = labelledByText(host);
              if (hostNamed) return hostNamed;
              const aria = (host.getAttribute("aria-label") || "").trim();
              if (isQuestionText(aria)) return aria;
              const title = visibleText(host.querySelector(titleSelector));
              if (isQuestionText(title)) return title;
            }
            return previousQuestion(element) || labelFor(element);
          };

          const isVisibleHost = (element) => {
            const host = element.closest(
              "fieldset, [role='radiogroup'], [data-test-form-element], "
              + ".fb-dash-form-element, label, body"
            );
            return Boolean(
              element.getClientRects().length
              || (host && host.getClientRects().length)
            );
          };

          const fromInputs = controls
            .filter(element => {
              const type = (element.getAttribute("type") || "text").toLowerCase();
              if (type === "hidden") return false;
              // LinkedIn hides native radios behind styled labels.
              if (type === "file" || type === "radio") return isVisibleHost(element);
              return element.getClientRects().length > 0;
            })
            .map((element, index) => {
              const tag = element.tagName.toLowerCase();
              const inputType = (element.getAttribute("type") || "text").toLowerCase();
              const type = tag === "textarea"
                ? "textarea"
                : tag === "select"
                  ? "select"
                  : inputType;
              const options = tag === "select"
                ? [...element.options].map(option => option.text.trim()).filter(Boolean)
                : type === "radio" && element.name
                  ? [...root.querySelectorAll(
                      `input[type="radio"][name="${CSS.escape(element.name)}"]`
                    )].map(radio => optionText(radio)).filter(Boolean)
                  : [];
              let label = type === "radio"
                ? questionLabel(element)
                : labelFor(element);
              if (type === "radio" && !isQuestionText(label)) {
                label = previousQuestion(element) || label;
              }
              const requiredMark = /\\*/.test(
                (element.closest("fieldset") || element).innerText || ""
              );
              return {
                id: type === "radio" && element.name
                  ? element.name
                  : (element.id || element.name || `field-${index}`),
                name: element.name || "",
                label,
                type,
                options,
                required: element.required
                  || element.getAttribute("aria-required") === "true"
                  || (type === "radio" && requiredMark)
              };
            });

          const seen = new Set(
            fromInputs.flatMap((field) => [field.name, field.label].filter(Boolean))
          );
          const extras = [];
          for (const box of root.querySelectorAll(
            "fieldset, .fb-dash-form-element, "
            + ".jobs-easy-apply-form-section__grouping, [role='radiogroup']"
          )) {
            const choices = [...box.querySelectorAll(
              "[data-test-text-selectable-option], .fb-text-selectable__option, "
              + ".fb-text-selectableoption, [role='radio'], input[type='radio']"
            )];
            if (choices.length < 2) continue;
            let label = visibleText(box.querySelector(titleSelector));
            if (!isQuestionText(label)) {
              label = labelledByText(box) || previousQuestion(box);
            }
            if (!isQuestionText(label)) continue;
            const name = box.querySelector("input[type='radio']")?.name || label;
            if (seen.has(name) || seen.has(label)) continue;
            seen.add(name);
            seen.add(label);
            extras.push({
              id: name,
              name,
              label,
              type: "radio",
              options: choices.map((choice) => (
                visibleText(choice)
                || choice.getAttribute("data-test-text-selectable-option__input")
                || choice.value
                || ""
              )).filter(Boolean),
              required: /\\*/.test(box.innerText || ""),
            });
          }
          return [...fromInputs, ...extras];
        }
        """
    )

    fields: list[FormField] = []
    radio_groups: set[str] = set()
    used_ids: set[str] = set()
    for index, raw in enumerate(raw_fields):
        raw_type = str(raw.get("type", "text")).lower()
        normalized_type: FormFieldType
        if raw_type in _SUPPORTED_INPUT_TYPES:
            normalized_type = raw_type  # type: ignore[assignment]
        elif raw_type in {"textarea", "select"}:
            normalized_type = raw_type  # type: ignore[assignment]
        else:
            normalized_type = "text"

        group_name = str(raw.get("name", ""))
        if normalized_type == "radio" and group_name:
            if group_name in radio_groups:
                continue
            radio_groups.add(group_name)

        field_id = str(raw.get("id") or f"field-{index}")
        if field_id in used_ids:
            field_id = f"{field_id}-{index}"
        used_ids.add(field_id)

        fields.append(
            FormField(
                id=field_id,
                label=str(raw.get("label") or field_id),
                type=normalized_type,
                options=[str(option) for option in raw.get("options", [])],
                required=bool(raw.get("required", False)),
            )
        )

    step_label: str | None = None
    heading = root.locator("h1, h2, h3, [role='heading']").first
    if heading.count():
        candidate = heading.inner_text().strip()
        step_label = candidate or None

    return Form(
        url=url or page.url,
        step_label=step_label,
        fields=fields,
    )


def upload(page: Page, selector: str, path: str | Path) -> None:
    """Upload an existing file to a file input."""

    upload_path = Path(path).expanduser().resolve()
    if not upload_path.is_file():
        raise FileNotFoundError(f"Upload file not found: {upload_path}")
    page.locator(selector).set_input_files(str(upload_path))
