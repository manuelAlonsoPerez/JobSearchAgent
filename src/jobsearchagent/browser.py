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


def page_to_form(page: Page, url: str | None = None) -> Form:
    """Normalize the visible dialog/form controls on the current page."""

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

          const labelFor = (element) => {
            const labels = element.labels ? [...element.labels] : [];
            const labelText = labels.map(label => label.innerText.trim())
              .filter(Boolean).join(" ");
            return labelText
              || element.getAttribute("aria-label")
              || element.getAttribute("placeholder")
              || element.getAttribute("name")
              || element.id
              || "";
          };

          return controls
            .filter(element => {
              const type = (element.getAttribute("type") || "text").toLowerCase();
              return type === "file" || element.getClientRects().length > 0;
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
                    )].map(radio => labelFor(radio)).filter(Boolean)
                  : [];
              return {
                id: element.id || element.name || `field-${index}`,
                name: element.name || "",
                label: labelFor(element),
                type,
                options,
                required: element.required
                  || element.getAttribute("aria-required") === "true"
              };
            });
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
