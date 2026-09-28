"""The engine must behave identically whichever compiler produced the contract.

Two compilers exist for one YAML dialect: C2's `veyra_contracts.compile` (authoritative) and the
engine's own `veyra_engine.testing.mini_compile` (a test-only stand-in from A3, kept because
`veyra_contracts` depends on `veyra-engine` — importing it from engine tests would make the lower
layer's tests depend on the higher one).

That is a drift risk, so it gets a test rather than a promise: for every contract we have, compile
it both ways and assert the **engine** cannot tell the difference — same tier, same template, same
mapped values and the same byte offsets. Differences in the compiled *representation* are fine and
expected (C2 emits explicit nulls, labels capture types differently, and its IP regex is stricter);
differences in behaviour are not.

If this test fails, `veyra_contracts.compile` is right and `mini_compile` is wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.models import Envelope
from veyra_common.settings import contracts_repo_path
from veyra_contracts import compile as compile_contract
from veyra_engine import Engine, EngineContext, mini_compile, provenance_check, serialize

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
TEST_CONTRACTS = Path(__file__).resolve().parent / "contracts"
SEED_CONTRACTS = contracts_repo_path() / "t_ntro_core"
RECEIVED = "2026-09-26T14:10:00.000000000Z"

# contract file -> (source_id, tenant, vendor, corpus file to replay through it)
CASES: list[tuple[Path, str, str, str, str]] = [
    (
        TEST_CONTRACTS / "authsrv.yaml",
        "src_authsrv_01",
        "t_maha_power",
        "custom",
        "authsrv_t1_ok.log",
    ),
    (
        TEST_CONTRACTS / "authsrv_v2.yaml",
        "src_authsrv_01",
        "t_maha_power",
        "custom",
        "authsrv_t3_failed.log",
    ),
    (
        SEED_CONTRACTS / "linux_sshd.yaml",
        "src_lnx_core_07",
        "t_ntro_core",
        "linux",
        "linux_sshd.log",
    ),
    (
        SEED_CONTRACTS / "acme_ngfw_cef.yaml",
        "src_fw_dmz_01",
        "t_ntro_core",
        "acme_ngfw",
        "acme_ngfw_cef.log",
    ),
]


def available(case: tuple[Path, str, str, str, str]) -> bool:
    return case[0].is_file()


def engine_for(compiled: dict[str, Any]) -> Engine:
    engine = Engine(EngineContext())
    engine.load([compiled])
    return engine


def envelopes(corpus_file: str, source_id: str, tenant: str, vendor: str) -> list[Envelope]:
    raws = [framed.raw for framed in split_lines((CORPUS / corpus_file).read_bytes())]
    return [
        stamp(
            raw,
            collector_id="parity",
            transport="syslog_tcp",
            framing_method="newline",
            source_id=source_id,
            tenant_id=tenant,
            vendor=vendor,
            zone="dmz",
            event_uid=f"0192a4f0-0000-7000-8000-{index:012d}",
            received_time=RECEIVED,
        )
        for index, raw in enumerate(raws)
    ]


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, id=c[0].name) for c in CASES],
)
def test_both_compilers_produce_engine_identical_results(
    case: tuple[Path, str, str, str, str],
) -> None:
    contract_path, source_id, tenant, vendor, corpus_file = case
    if not contract_path.is_file():
        pytest.skip(f"needs the contracts repository checked out at {contract_path.parents[1]}")

    yaml_text = contract_path.read_text()
    mine = engine_for(mini_compile(yaml_text))
    theirs = engine_for(compile_contract(yaml_text).model_dump())

    for envelope in envelopes(corpus_file, source_id, tenant, vendor):
        left = mine.normalize(envelope)
        right = theirs.normalize(envelope)

        assert left.tier == right.tier, f"tier differs on {envelope.event_uid}"
        assert left.conformance == right.conformance
        assert left.category == right.category
        assert left.ulpf["template"]["id"] == right.ulpf["template"]["id"]
        assert left.ulpf["template"]["sig"] == right.ulpf["template"]["sig"]

        # The bytes each offset points at must match, not merely the offsets themselves.
        assert left.ulpf["field_offsets"] == right.ulpf["field_offsets"], "offsets differ"
        assert left.ulpf["derived_fields"] == right.ulpf["derived_fields"]

        # And the delivered event must be byte-identical once serialized.
        assert serialize(left.ocsf) == serialize(right.ocsf), "serialized events differ"

        if left.tier == 1:
            assert all(check.ok for check in provenance_check(right.ocsf, envelope.raw_bytes))


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, id=c[0].name) for c in CASES],
)
def test_compiled_shapes_agree_where_the_engine_reads_them(
    case: tuple[Path, str, str, str, str],
) -> None:
    """The fields the engine actually consumes must match; representation may differ."""
    contract_path = case[0]
    if not contract_path.is_file():
        pytest.skip(f"needs the contracts repository checked out at {contract_path.parents[1]}")

    yaml_text = contract_path.read_text()
    mine = mini_compile(yaml_text)
    theirs = compile_contract(yaml_text).model_dump()

    for key in ("contract", "version", "tenant", "sources", "envelope", "required", "pii", "vocab"):
        assert mine.get(key) == theirs.get(key), f"{key} differs"

    assert len(mine["templates"]) == len(theirs["templates"])
    for left, right in zip(mine["templates"], theirs["templates"], strict=True):
        for key in ("id", "class_uid", "activity_id", "type_uid", "category", "unmapped"):
            assert left[key] == right[key], f"templates[{left['id']}].{key} differs"
        # Capture names must match; the type label is cosmetic (C2 says "string", A3 said "token").
        assert [c["name"] for c in left["captures"]] == [c["name"] for c in right["captures"]]

        # Map entries: same paths, same kinds, same refs — C2 additionally emits explicit nulls.
        def norm(entries: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
            out = []
            for e in entries:
                kind = e["kind"]
                # For kind "text" the engine ignores `ref` entirely (the value is the text field),
                # so C2 emitting ref="__text" where mini_compile emits None is representation,
                # not behaviour — and the behavioural test above already proves that.
                ref = None if kind == "text" else (e.get("ref") or None)
                out.append((e["ocsf_path"], kind, ref, e.get("value"), e.get("vocab")))
            return out

        assert norm(left["map"]) == norm(right["map"]), f"templates[{left['id']}].map differs"


def test_mini_compile_is_documented_as_the_stand_in() -> None:
    """Nobody should mistake the test compiler for the production one."""
    import veyra_engine.testing as testing

    doc = (testing.__doc__ or "").lower()
    assert "veyra_contracts" in doc
    assert "test" in doc
