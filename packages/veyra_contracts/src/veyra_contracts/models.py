"""Pydantic models mirroring IF-CONTRACT-YAML exactly. Unknown keys are an error."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
TenantId = Annotated[str, StringConstraints(pattern=r"^t_[a-z0-9_]+$")]
SourceId = Annotated[str, StringConstraints(pattern=r"^src_[a-z0-9_]+$")]
ContractState = Literal["draft", "testing", "canary", "active", "retired"]

LAYERS: frozenset[str] = frozenset(
    {"syslog", "json", "kv", "cef", "leef", "csv", "regex", "base64"}
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ConstValue(_Strict):
    const: str | int | float | bool


class VocabValue(_Strict):
    vocab: str
    from_: str = Field(alias="from")


class TsValue(_Strict):
    ts: str
    formats: list[str] = Field(default_factory=list)


MapValue = str | ConstValue | VocabValue | TsValue


class TimeSpec(_Strict):
    field: str | None = None
    formats: list[str] = Field(default_factory=list)
    timezone: str | None = None
    year: Literal["infer_from_received", "present"] = "present"


class TemplateSpec(_Strict):
    id: Slug
    pattern: str = Field(min_length=1)
    class_: str = Field(alias="class")
    activity: str
    map: dict[str, MapValue] = Field(default_factory=dict)
    unmapped: list[str] = Field(default_factory=list)

    @field_validator("map")
    @classmethod
    def _references_start_with_dollar(cls, value: dict[str, MapValue]) -> dict[str, MapValue]:
        for path, item in value.items():
            if isinstance(item, str) and not item.startswith("$"):
                raise ValueError(
                    f"map[{path}]: a reference must start with '$' (got {item!r}); "
                    "use {const: ...} for a literal value"
                )
        return value


class TestCase(_Strict):
    sample: str
    expect: str


class Provenance(_Strict):
    drafted_by: str
    draft_id: str | None = None
    approved_by: list[str] = Field(default_factory=list)


class ContractYaml(_Strict):
    contract: Slug
    version: int = Field(ge=1)
    tenant: TenantId
    sources: list[SourceId] = Field(default_factory=list)
    description: str = ""
    state: ContractState = "draft"
    envelope: list[dict[str, dict[str, Any]]] = Field(default_factory=list)
    time: TimeSpec | None = None
    templates: list[TemplateSpec] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)
    enrich: list[str] = Field(default_factory=list)
    pii: list[str] = Field(default_factory=list)
    vocab: list[str] = Field(default_factory=list)
    tests: list[TestCase] = Field(default_factory=list)
    provenance: Provenance | None = None

    @field_validator("envelope")
    @classmethod
    def _one_known_layer_each(
        cls, value: list[dict[str, dict[str, Any]]]
    ) -> list[dict[str, dict[str, Any]]]:
        for index, layer in enumerate(value):
            if len(layer) != 1:
                raise ValueError(f"envelope[{index}] must name exactly one layer")
            (name,) = layer
            if name not in LAYERS:
                raise ValueError(
                    f"envelope[{index}]: unknown layer {name!r}; expected one of {sorted(LAYERS)}"
                )
        return value

    @model_validator(mode="after")
    def _unique_template_ids(self) -> ContractYaml:
        ids = [t.id for t in self.templates]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate template ids: {duplicates}")
        return self
