"""``compile(yaml_text) -> CompiledContract``: IF-CONTRACT-YAML to IF-CONTRACT-COMPILED.

Pure and deterministic: no clock, no I/O. The same YAML and ``compiled_at`` always give
byte-identical ``to_json()`` output (C2 AC1, decision TC1).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, NoReturn

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from veyra_contracts.catalogue import CLASSES, ENUMS, FIELDS
from veyra_contracts.errors import ContractError
from veyra_contracts.models import (
    ConstValue,
    ContractYaml,
    MapValue,
    TemplateSpec,
    TsValue,
    VocabValue,
)
from veyra_contracts.pattern import compile_pattern
from veyra_contracts.positions import Positions, YamlPath, locate, node_positions

COMPILER_VERSION = "0.1.0"
EPOCH = "1970-01-01T00:00:00.000000000Z"

MapKind = Literal["capture", "field", "const", "vocab", "ts", "text"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompiledCapture(_Out):
    name: str
    type: str


class MapEntry(_Out):
    ocsf_path: str
    kind: MapKind
    ref: str | None = None
    value: Any = None
    vocab: str | None = None


class CompiledTemplate(_Out):
    id: str
    regex: str
    captures: list[CompiledCapture]
    class_uid: int
    activity_id: int
    type_uid: int
    category: str
    map: list[MapEntry]
    unmapped: list[str]


class CompiledContract(_Out):
    contract: str
    version: int
    tenant: str
    sources: list[str]
    envelope: list[dict[str, dict[str, Any]]]
    time: dict[str, Any]
    templates: list[CompiledTemplate]
    required: list[str]
    enrich: list[str]
    pii: list[str]
    vocab: list[str]
    compiled_at: str
    compiler_version: str

    def to_dict(self) -> dict[str, Any]:
        """The IF-CONTRACT-COMPILED JSON object (what IF-CONTROL and the engine carry)."""
        return self.model_dump(mode="json")

    def to_json(self) -> bytes:
        """Canonical bytes: sorted keys, compact separators, UTF-8."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")


def compile(yaml_text: str, *, compiled_at: str = EPOCH) -> CompiledContract:
    """Validate and compile one Log Contract. Raises :class:`ContractError`."""
    positions = _positions(yaml_text)
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ContractError("a Log Contract must be a YAML mapping", line=1, column=1)
    try:
        spec = ContractYaml.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = tuple(first["loc"])
        line, column = locate(positions, loc)
        raise ContractError(f"{_dotted(loc)}: {first['msg']}", line=line, column=column) from exc
    return _Compiler(spec, positions).run(compiled_at)


def _positions(yaml_text: str) -> Positions:
    try:
        return node_positions(yaml_text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc)
        raise ContractError(
            f"invalid YAML: {problem}",
            line=mark.line + 1 if mark else None,
            column=mark.column + 1 if mark else None,
        ) from exc


def _dotted(loc: Sequence[str | int]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else part
    return out or "<root>"


class _Compiler:
    def __init__(self, spec: ContractYaml, positions: Positions) -> None:
        self.spec = spec
        self.positions = positions
        self.layers = {next(iter(layer)) for layer in spec.envelope}
        self.vocabs = set(spec.vocab)

    def fail(self, loc: YamlPath, message: str) -> NoReturn:
        line, column = locate(self.positions, loc)
        raise ContractError(f"{_dotted(loc)}: {message}", line=line, column=column)

    def run(self, compiled_at: str) -> CompiledContract:
        s = self.spec
        templates = [self.template(i, t) for i, t in enumerate(s.templates)]
        for key in ("required", "pii"):
            for index, path in enumerate(getattr(s, key)):
                if path not in FIELDS:
                    self.fail((key, index), f"{path!r} is not in the OCSF field catalogue")
        return CompiledContract(
            contract=s.contract,
            version=s.version,
            tenant=s.tenant,
            sources=list(s.sources),
            envelope=[dict(layer) for layer in s.envelope],
            time=s.time.model_dump() if s.time else {},
            templates=templates,
            required=list(s.required),
            enrich=list(s.enrich),
            pii=list(s.pii),
            vocab=list(s.vocab),
            compiled_at=compiled_at,
            compiler_version=COMPILER_VERSION,
        )

    def template(self, index: int, t: TemplateSpec) -> CompiledTemplate:
        where: YamlPath = ("templates", index)
        cls = CLASSES.get(t.class_)
        if cls is None:
            self.fail(
                (*where, "class"),
                f"unknown OCSF class {t.class_!r}; expected one of {sorted(CLASSES)}",
            )
        activity_id = cls.activities.get(t.activity)
        if activity_id is None:
            self.fail(
                (*where, "activity"),
                f"unknown activity {t.activity!r} for {cls.name}; "
                f"expected one of {sorted(cls.activities)}",
            )
        try:
            regex, captures = compile_pattern(t.pattern)
        except ContractError as exc:
            self.fail((*where, "pattern"), exc.message)
        names = {c.name for c in captures}
        entries = [self.map_entry(index, path, value, names) for path, value in t.map.items()]
        return CompiledTemplate(
            id=t.id,
            regex=regex,
            captures=[CompiledCapture(name=c.name, type=c.type) for c in captures],
            class_uid=cls.class_uid,
            activity_id=activity_id,
            type_uid=cls.class_uid * 100 + activity_id,
            category=cls.category,
            map=entries,
            unmapped=list(t.unmapped),
        )

    def map_entry(self, index: int, path: str, value: MapValue, names: set[str]) -> MapEntry:
        loc: YamlPath = ("templates", index, "map", path)
        if path not in FIELDS:
            self.fail(loc, f"{path!r} is not in the OCSF field catalogue")
        if isinstance(value, str):
            ref = value[1:]
            if ref == "__text":
                return MapEntry(ocsf_path=path, kind="text", ref="__text")
            if "." in ref:
                layer = ref.split(".", 1)[0]
                if layer not in self.layers:
                    self.fail(
                        loc,
                        f"{value!r} reads layer {layer!r}, which the envelope does not declare",
                    )
                return MapEntry(ocsf_path=path, kind="field", ref=ref)
            self._require_capture(loc, value, names)
            return MapEntry(ocsf_path=path, kind="capture", ref=ref)
        if isinstance(value, ConstValue):
            enum = ENUMS.get(path)
            if enum is not None and (isinstance(value.const, bool) or value.const not in enum):
                self.fail(loc, f"{value.const!r} is not a valid {path} (allowed: {sorted(enum)})")
            return MapEntry(ocsf_path=path, kind="const", value=value.const)
        if isinstance(value, VocabValue):
            if value.vocab not in self.vocabs:
                self.fail(loc, f"vocab {value.vocab!r} is not in the contract's vocab list")
            self._require_capture(loc, value.from_, names)
            return MapEntry(
                ocsf_path=path, kind="vocab", ref=value.from_.removeprefix("$"), vocab=value.vocab
            )
        assert isinstance(value, TsValue)
        self._require_capture(loc, value.ts, names)
        return MapEntry(
            ocsf_path=path, kind="ts", ref=value.ts.removeprefix("$"), value=list(value.formats)
        )

    def _require_capture(self, loc: YamlPath, reference: str, names: set[str]) -> None:
        if reference.removeprefix("$") not in names:
            self.fail(
                loc, f"{reference!r} is not a capture of this template (captures: {sorted(names)})"
            )
