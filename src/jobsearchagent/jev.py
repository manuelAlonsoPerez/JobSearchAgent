"""Typed decision client for Ollama's System One (JEV) endpoint."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from .config import get_settings

_RETRYABLE_STATUS = range(500, 600)
_DEFAULT_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0


class JevResponseError(ValueError):
    """Raised when System One returns an answer with an unexpected shape."""


class Jev:
    """Small synchronous client for ``POST /v1/systemone``."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.jev_model
        self._owns_client = client is None
        request_timeout = (
            timeout if timeout is not None else settings.jev_timeout_seconds
        )
        self._client = client or httpx.Client(timeout=request_timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Jev:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ask(
        self,
        state: str | Mapping[str, Any],
        questions: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Ask one or more typed questions and return the answer mapping."""

        if not questions:
            raise ValueError("At least one JEV question is required")

        payload = {
            "model": self.model,
            "state": state,
            "questions": dict(questions),
        }
        url = f"{self.base_url}/v1/systemone"
        last_error: Exception | None = None
        for attempt in range(1, _DEFAULT_RETRIES + 1):
            try:
                response = self._client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt == _DEFAULT_RETRIES:
                    raise
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            if response.status_code in _RETRYABLE_STATUS and attempt < _DEFAULT_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise JevResponseError(_http_error(exc)) from exc
            body = response.json()
            answers = body.get("answers")
            if not isinstance(answers, dict):
                raise JevResponseError("System One response has no 'answers' mapping")
            return answers
        if last_error is not None:
            raise last_error
        raise JevResponseError("JEV request failed after retries")

    def choice(
        self,
        state: str | Mapping[str, Any],
        instructions: str,
        options: Mapping[str, str],
    ) -> tuple[str, float]:
        """Return the selected option and confidence."""

        if len(options) < 2:
            raise ValueError("A choice question requires at least two options")
        answer = self._single_answer(
            state,
            {
                "type": "choice",
                "instructions": instructions,
                "criteria": dict(options),
            },
        )
        selected = answer.get("choice", answer.get("answer"))
        if selected not in options:
            raise JevResponseError(f"Unknown choice returned by System One: {selected!r}")

        probabilities = answer.get("probabilities")
        fallback = (
            max((float(value) for value in probabilities.values()), default=0.0)
            if isinstance(probabilities, dict)
            else 0.0
        )
        confidence = float(answer.get("confidence", fallback))
        return str(selected), confidence

    def noul(
        self,
        state: str | Mapping[str, Any],
        instructions: str,
    ) -> float:
        """Return P(true) for a binary decision."""

        answer = self._single_answer(
            state,
            {"type": "noul", "instructions": instructions},
        )
        value = answer.get(
            "noul",
            answer.get("probability", answer.get("true")),
        )
        if value is None and isinstance(answer.get("answer"), bool):
            value = 1.0 if answer["answer"] else 0.0
        return self._probability(value, "noul")

    def score(
        self,
        state: str | Mapping[str, Any],
        instructions: str,
        levels: list[str],
    ) -> float:
        """Return the probability-weighted score for ordered levels."""

        if not 2 <= len(levels) <= 10:
            raise ValueError("A score question requires between 2 and 10 levels")
        answer = self._single_answer(
            state,
            {
                "type": "score",
                "instructions": instructions,
                "criteria": levels,
            },
        )
        value = answer.get("score", answer.get("value"))
        if value is None:
            raise JevResponseError("Score answer has neither 'score' nor 'value'")
        return float(value)

    def _single_answer(
        self,
        state: str | Mapping[str, Any],
        question: Mapping[str, Any],
    ) -> dict[str, Any]:
        answer = self.ask(state, {"q": question}).get("q")
        if not isinstance(answer, dict):
            raise JevResponseError("System One response has no answer named 'q'")
        return answer

    @staticmethod
    def _probability(value: Any, answer_type: str) -> float:
        if value is None:
            raise JevResponseError(f"{answer_type} answer has no probability")
        probability = float(value)
        if not 0.0 <= probability <= 1.0:
            raise JevResponseError(
                f"{answer_type} probability must be between 0 and 1"
            )
        return probability


def _http_error(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    body = (response.text or "").strip().replace("\n", " ")
    if len(body) > 300:
        body = body[:297] + "..."
    detail = body or response.reason_phrase or "no response body"
    return f"JEV HTTP {response.status_code} from {response.request.url}: {detail}"
