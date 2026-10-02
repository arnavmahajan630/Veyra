"""IF-LLM-DRAFT: the response every drafter (LLM, cache, heuristic) must produce.

The model may only point at token ids from the request or pick a ``const`` from an enum;
never free text. ``request_schema()`` says so in the schema a model is constrained to: the
ids of this request, and for each one only the fields its value could fill. ``problems()``
checks the same afterwards, plus what a schema can't say (a path mapped twice, an activity
that belongs to another class), and guards the cache and any model that ignores ``format``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from veyra_contracts.catalogue import CLASSES, ENUMS, FIELDS
from veyra_contracts.drafting.options import CONST_PATHS, paths_for
from veyra_engine import Token

Confidence = Literal["high", "medium", "low"]


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ocsf_path: str
    token: str | None = None
    const: int | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> Mapping:
        if (self.token is None) == (self.const is None):
            raise ValueError(f"{self.ocsf_path}: give exactly one of token or const")
        return self


class DraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_: str = Field(alias="class")
    activity: str
    confidence: Confidence = "medium"
    mappings: list[Mapping] = Field(default_factory=list)
    rationale: str = ""

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


def response_schema() -> dict[str, Any]:
    """The shape of a response, with no request in hand: any string where an id or path goes."""
    return DraftResponse.model_json_schema(by_alias=True)


def _mapping(path: dict[str, Any], key: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"ocsf_path": path, key: value},
        "required": ["ocsf_path", key],
        "additionalProperties": False,
    }


def request_schema(tokens: Iterable[Token]) -> dict[str, Any]:
    """The JSON schema a model is constrained to for one request (Ollama's ``format``).

    ``tokens`` are the request's variable tokens. A mapping is one of: this token id with one
    of the fields its value could fill (``options.paths_for``), or an enum path with one of
    its constants. Single-value ``enum`` rather than ``const``, which every grammar-based
    constrainer understands.

    ``maxItems`` is one mapping per token plus one per enum path. Without it a small model
    can repeat a mapping until the timeout, since the grammar never makes it stop.
    """
    tokens = list(tokens)
    branches = [
        _mapping(
            {"type": "string", "enum": list(paths_for(token))},
            "token",
            {"type": "string", "enum": [token.id]},
        )
        for token in tokens
    ]
    branches += [
        _mapping(
            {"type": "string", "enum": [path]},
            "const",
            {"type": "integer", "enum": sorted(ENUMS[path])},
        )
        for path in CONST_PATHS
    ]
    activities = sorted({activity for cls in CLASSES.values() for activity in cls.activities})
    return {
        "type": "object",
        "properties": {
            "class": {"type": "string", "enum": list(CLASSES)},
            "activity": {"type": "string", "enum": activities},
            "confidence": {"type": "string", "enum": list(get_args(Confidence))},
            "mappings": {
                "type": "array",
                "items": {"anyOf": branches},
                "maxItems": len(tokens) + len(CONST_PATHS),
            },
            "rationale": {"type": "string"},
        },
        "required": ["class", "activity", "confidence", "mappings", "rationale"],
        "additionalProperties": False,
    }


def without_repeats(response: DraftResponse) -> DraftResponse:
    """The response with a mapping that is said twice, word for word, kept once.

    Repeating itself is how a constrained small model fills an array; it is not a conflict.
    The same path with two *different* sources still is, and ``problems()`` reports it.
    """
    kept: list[Mapping] = []
    for mapping in response.mappings:
        if mapping not in kept:
            kept.append(mapping)
    return response.model_copy(update={"mappings": kept})


def problems(response: DraftResponse, token_ids: set[str]) -> list[str]:
    """Everything outside the closed vocabulary; empty means the response is usable."""
    out: list[str] = []
    cls = CLASSES.get(response.class_)
    if cls is None:
        out.append(f"unknown class {response.class_!r}")
    elif response.activity not in cls.activities:
        out.append(f"activity {response.activity!r} is not valid for {response.class_}")
    seen: set[str] = set()
    for mapping in response.mappings:
        path = mapping.ocsf_path
        if path in seen:
            out.append(f"{path} is mapped twice")
        seen.add(path)
        if path not in FIELDS:
            out.append(f"{path} is not in the OCSF field catalogue")
        if mapping.token is not None and mapping.token not in token_ids:
            out.append(f"{path}: token {mapping.token!r} is not one of the offered tokens")
        if mapping.const is not None and mapping.const not in ENUMS.get(path, {}):
            out.append(f"{path}: const {mapping.const!r} is not an allowed enum value")
    return out
