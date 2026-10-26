"""Apply a resolved FieldAnswer to a live Easy Apply control."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..models import FieldAnswer, FormField

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


def apply_answer(
    page: Page,
    root: Locator,
    field: FormField,
    answer: FieldAnswer,
) -> bool:
    """Write ``answer`` into the matching control. Return whether a write ran."""

    if answer.value is None or answer.source == "deferred":
        return False

    control = locate_control(root, field)
    if control is None:
        return False

    value = answer.value
    try:
        if field.type == "file":
            path = Path(str(value)).expanduser()
            if not path.is_file():
                return False
            control.set_input_files(str(path))
            return True
        if field.type == "checkbox":
            control.set_checked(bool(value) and str(value).casefold() not in {"no", "false"})
            return True
        if field.type == "radio":
            option = root.get_by_label(str(value), exact=False)
            if option.count():
                option.first.check()
                return True
            control.check()
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


def current_value(root: Locator, field: FormField) -> str | None:
    """Read the current string value of a control, if any."""

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
        return str(control.input_value())
    except Exception:
        return None
