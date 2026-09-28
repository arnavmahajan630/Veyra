"""Ollama ``/api/chat`` with schema-constrained output (C4 "Ollama call").

``temperature 0`` and ``seed 7`` are fixed by C4 so a draft is reproducible. An answer
that is not valid JSON, fails the response schema, or points outside the closed
vocabulary (``schema.problems``) is retried once with the complaint appended; a second
failure, or any transport error or timeout, raises ``DraftFailed``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from veyra_contracts.drafting.prompt import messages
from veyra_contracts.drafting.request import Prepared
from veyra_contracts.drafting.schema import DraftResponse, problems, response_schema

TEMPERATURE = 0
SEED = 7


class DraftFailed(RuntimeError):
    """The model did not produce a usable draft."""


class OllamaClient:
    def __init__(
        self,
        url: str,
        model: str,
        *,
        num_ctx: int,
        timeout_s: float,
        keep_alive: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._num_ctx = num_ctx
        self._keep_alive = keep_alive
        self._client = client or httpx.Client(base_url=url, timeout=timeout_s)

    def _chat(self, chat: list[dict[str, str]]) -> str:
        body = {
            "model": self.model,
            "messages": chat,
            "format": response_schema(),
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": TEMPERATURE, "seed": SEED, "num_ctx": self._num_ctx},
        }
        try:
            response = self._client.post("/api/chat", json=body)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise DraftFailed(f"the model timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise DraftFailed(f"ollama unavailable: {exc}") from exc
        content: str = response.json()["message"]["content"]
        return content

    @staticmethod
    def _parse(content: str, token_ids: set[str]) -> tuple[DraftResponse | None, str]:
        try:
            parsed = DraftResponse.model_validate(json.loads(content))
        except json.JSONDecodeError:
            return None, "the answer is not valid JSON"
        except ValidationError as exc:
            return None, f"the answer does not fit the schema: {exc.errors()[0]['msg']}"
        found = problems(parsed, token_ids)
        return (None, "; ".join(found)) if found else (parsed, "")

    def draft(self, prepared: Prepared) -> tuple[DraftResponse, str]:
        token_ids = {t["id"] for t in prepared.request["tokens"]}
        complaint: str | None = None
        for _ in range(2):
            content = self._chat(messages(prepared.request, complaint=complaint))
            parsed, complaint = self._parse(content, token_ids)
            if parsed is not None:
                return parsed, content
        raise DraftFailed(complaint or "no usable answer")

    def warm(self) -> None:
        """Load the model and keep it resident (``make llm-warm``)."""
        self._client.post(
            "/api/generate",
            json={
                "model": self.model,
                "prompt": "ok",
                "keep_alive": self._keep_alive,
                "stream": False,
            },
        ).raise_for_status()

    def ps(self) -> list[dict[str, Any]]:
        """Loaded models and their VRAM (``GET /api/ps``), for the bench report."""
        response = self._client.get("/api/ps")
        response.raise_for_status()
        models: list[dict[str, Any]] = response.json().get("models", [])
        return models
