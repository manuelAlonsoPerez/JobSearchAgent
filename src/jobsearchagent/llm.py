"""Generative Ollama client used by writers and apply reasoning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from .config import get_settings


Message = Mapping[str, Any]
OutputFormat = str | Mapping[str, Any]


def chat(
    messages: Sequence[Message],
    model: str | None = None,
    format: OutputFormat | None = None,
    *,
    timeout: float = 300.0,
    client: httpx.Client | None = None,
) -> str:
    """Generate one non-streaming Ollama chat response.

    ``model=None`` or ``model="writer"`` selects ``WRITER_MODEL``.
    ``model="apply"`` selects ``APPLY_MODEL``. A concrete Ollama model name
    can also be passed directly.
    """

    if not messages:
        raise ValueError("At least one chat message is required")

    settings = get_settings()
    selected_model = {
        None: settings.writer_model,
        "writer": settings.writer_model,
        "apply": settings.apply_model,
    }.get(model, model)

    payload: dict[str, Any] = {
        "model": selected_model,
        "messages": [dict(message) for message in messages],
        "stream": False,
    }
    if format is not None:
        payload["format"] = format

    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout)
    try:
        response = http_client.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    finally:
        if owns_client:
            http_client.close()

    message = body.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("Ollama response has no string 'message.content'")
    return message["content"]
