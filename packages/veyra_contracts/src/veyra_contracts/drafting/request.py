"""Build the IF-LLM-DRAFT request from real samples (C4 "Request building").

1. Peel every sample to its template text (the contract's layers, or ``classify``).
2. ``extract_tokens`` on the first sample's text. A token is **variable** when its kind is
   a value kind (ip, int, user, …) or when its value differs at the same position in the
   other samples (aligned by token index when the samples tokenize alike).
3. The model sees only variable tokens, with user and email values replaced by
   ``<USER_n>``/``<EMAIL_n>``, and samples masked the same way. Token ids still point at
   real spans of the first sample, which is what makes provenance hold by construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from veyra_contracts.catalogue import CLASSES, ENUMS, FIELDS
from veyra_contracts.drafting.classify import Layers, Peeled, classify, peel
from veyra_engine import Token, extract_tokens, mask

VALUE_KINDS = frozenset(
    {
        "ip",
        "ipv6",
        "port",
        "email",
        "url",
        "hostname",
        "user",
        "hash",
        "uuid",
        "timestamp",
        "int",
        "kv_value",
        "quoted",
    }
)
PERSONAL_KINDS = {"user": "USER", "email": "EMAIL"}


@dataclass
class Prepared:
    """Everything the drafter steps share; ``request`` is the part the LLM sees."""

    template_sig: str
    drain_template: str
    layers: Layers
    samples: list[Peeled]
    tokens: list[Token]  # of samples[0].template_text
    variable: set[str]  # token ids
    request: dict[str, Any]

    @property
    def text(self) -> str:
        return self.samples[0].template_text

    def token(self, token_id: str) -> Token:
        return next(t for t in self.tokens if t.id == token_id)


def variable_ids(tokens: list[Token], others: Sequence[list[Token]]) -> set[str]:
    variable = {t.id for t in tokens if t.kind in VALUE_KINDS}
    for other in others:
        if [t.kind for t in other] != [t.kind for t in tokens]:
            continue  # not aligned; the kinds alone decide
        variable |= {t.id for t, o in zip(tokens, other, strict=True) if t.value != o.value}
    return variable


def _placeholders(samples: list[Peeled]) -> dict[str, str]:
    """Personal values across all samples → ``<USER_1>``, ``<EMAIL_1>``, … (stable order)."""
    names: dict[str, str] = {}
    counters: dict[str, int] = {}
    for sample in samples:
        for token in extract_tokens(sample.template_text):
            label = PERSONAL_KINDS.get(token.kind)
            if label and token.value not in names:
                counters[label] = counters.get(label, 0) + 1
                names[token.value] = f"<{label}_{counters[label]}>"
    return names


def _masked(text: str, names: dict[str, str]) -> str:
    for value in sorted(names, key=len, reverse=True):
        text = text.replace(value, names[value])
    return mask(text)[0]


def build(
    raws: Sequence[bytes],
    *,
    template_sig: str,
    drain_template: str = "",
    layers: Layers | None = None,
    max_depth: int = 4,
) -> Prepared:
    if not raws:
        raise ValueError("drafting needs at least one sample")
    if layers is None:
        layers = classify(raws[0].decode("utf-8", errors="replace"))
    samples = [peel(raw, layers, max_depth=max_depth) for raw in raws]
    tokens = extract_tokens(samples[0].template_text)
    others = [extract_tokens(s.template_text) for s in samples[1:]]
    variable = variable_ids(tokens, others)
    names = _placeholders(samples)
    request = {
        "template_sig": template_sig,
        "drain_template": drain_template,
        "samples_masked": [_masked(s.template_text, names) for s in samples],
        "tokens": [
            {"id": t.id, "value": names.get(t.value, t.value), "kind": t.kind}
            | ({"key": t.key} if t.key else {})
            for t in tokens
            if t.id in variable
        ],
        "allowed_classes": {c.name: sorted(c.activities) for c in CLASSES.values()},
        "allowed_fields": sorted(FIELDS),
        "enums": {path: {str(k): v for k, v in values.items()} for path, values in ENUMS.items()},
    }
    return Prepared(template_sig, drain_template, layers, samples, tokens, variable, request)


def display_template(prepared: Prepared) -> str:
    """The first sample with every variable token as ``<*>`` (Drain-style, for display)."""
    text, out, cursor = prepared.text, [], 0
    for token in prepared.tokens:
        if token.id in prepared.variable:
            out.append(text[cursor : token.start] + "<*>")
            cursor = token.end
    return "".join(out) + text[cursor:]
