"""Which model drafts (C4): the one place that turns settings into a model client.

Both clients satisfy ``drafter.Model`` and add ``warm()`` and ``ps()`` for the bench.
"""

from __future__ import annotations

from veyra_common.settings import Settings
from veyra_contracts.drafting.decision import DecisionClient
from veyra_contracts.drafting.ollama import OllamaClient

DECISION_PREFIX = "decision:"


def make_client(cfg: Settings, *, model: str | None = None) -> OllamaClient | DecisionClient:
    """The configured backend's client. ``model`` overrides the configured name; a
    ``decision:`` prefix on it picks the decision backend whatever ``llm_backend`` says, so
    a bench can line both kinds up in one run."""
    backend = cfg.llm_backend
    if model is not None and model.startswith(DECISION_PREFIX):
        backend, model = "decision", model.removeprefix(DECISION_PREFIX)
    if backend == "decision":
        return DecisionClient(
            cfg.decision_url,
            model or cfg.decision_model,
            timeout_s=cfg.llm_timeout_s,
            keep_alive=cfg.llm_keep_alive,
            min_probability=cfg.decision_min_probability,
        )
    return OllamaClient(
        cfg.ollama_url,
        model or cfg.llm_model,
        num_ctx=cfg.llm_num_ctx,
        timeout_s=cfg.llm_timeout_s,
        keep_alive=cfg.llm_keep_alive,
    )
