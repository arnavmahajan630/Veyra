"""IF-LLM-DRAFT: the response every drafter (LLM, cache, heuristic) must produce.

The model may only point at token ids from the request or pick a ``const`` from an enum;
never free text. ``problems()`` enforces that after JSON-schema validation, because a
schema can't say "one of these ids".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from veyra_contracts.catalogue import CLASSES, ENUMS, FIELDS

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
    """The JSON schema Ollama constrains its output to (``format``)."""
    return DraftResponse.model_json_schema(by_alias=True)


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
