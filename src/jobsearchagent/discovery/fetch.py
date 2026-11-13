"""Shared Playwright reads for the public-feed adapters."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from html import unescape
from typing import Any

from .linkedin import posted_window_seconds

_BLOCK_TAG = re.compile(r"</(?:p|div|h[1-6]|li|tr|ul)\s*>|<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def read_response(
    page: Any,
    url: str,
    output: Callable[[str], None],
    label: str,
) -> Any | None:
    """Return parsed JSON when the body is JSON, otherwise the response text."""

    request = getattr(page, "request", None)
    get = getattr(request, "get", None)
    if get is None:
        output(f"{label} has no browser request client.")
        return None
    try:
        response = get(url, timeout=60_000)
        status = int(getattr(response, "status", 0) or 0)
        if not getattr(response, "ok", False):
            output(f"{label} returned HTTP {status}; no jobs collected.")
            return None
        if callable(getattr(response, "json", None)):
            try:
                return response.json()
            except Exception:
                pass
        body = response.text() if callable(getattr(response, "text", None)) else response.text
    except Exception as exc:
        output(f"{label} could not be read: {exc}")
        return None
    if not isinstance(body, str) or not body.strip():
        return None
    stripped = body.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return body


def html_to_text(value: str) -> str:
    from ..utils.text import clean_job_text

    if not value or not value.strip():
        return ""
    text = unescape(value)
    text = _BLOCK_TAG.sub("\n", text)
    text = _TAG.sub("", text)
    return clean_job_text(unescape(text))


def max_age_seconds(filters: dict[str, Any]) -> int | None:
    if "month" not in filters or filters.get("month") is None:
        return None
    return posted_window_seconds(month=int(filters["month"]))
