"""Heuristic → pattern → YAML → verification, on the demo's real T1 and T3 lines (C4 AC1, AC3)."""

from __future__ import annotations

from pathlib import Path

from veyra_common.framing import split_lines
from veyra_contracts import compile
from veyra_contracts.drafting.generalize import add_template, generalize, new_contract
from veyra_contracts.drafting.heuristic import heuristic
from veyra_contracts.drafting.request import build
from veyra_contracts.drafting.schema import DraftResponse
from veyra_contracts.drafting.verify import verify

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
AUTHSRV_V1 = (
    REPO / "packages" / "veyra_engine" / "tests" / "contracts" / "authsrv.yaml"
).read_text(encoding="utf-8")


def lines(name: str, count: int = 8) -> list[bytes]:
    return [f.raw for f in split_lines((CORPUS / name).read_bytes())][:count]


def t3():  # type: ignore[no-untyped-def]
    return build(lines("authsrv_t3_failed.log"), template_sig="t_3c85a1bfbf81")


def test_the_heuristic_drafts_the_t3_mapping() -> None:
    """C4 AC1, heuristic mode."""
    response = heuristic(t3())
    assert (response.class_, response.activity) == ("authentication", "logon")
    by_path = {m.ocsf_path: m for m in response.mappings}
    prepared = t3()
    assert prepared.token(by_path["user.name"].token or "").value == "a.sharma"
    assert prepared.token(by_path["src_endpoint.ip"].token or "").value == "103.21.4.77"
    assert prepared.token(by_path["dst_endpoint.ip"].token or "").value == "10.2.3.4"
    assert by_path["status_id"].const == 2


def test_the_pattern_reads_like_a_humans() -> None:
    template = generalize(t3(), heuristic(t3()))
    assert template.pattern == (
        "user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>"
    )
    assert template.id == "logon_failed" and template.unmapped == ["attempts"]
    assert template.map["message"] == "$__text"


def test_v2_of_authsrv_compiles_and_verifies_on_the_raw_lines() -> None:
    prepared = t3()
    template = generalize(prepared, heuristic(prepared))
    v2 = add_template(AUTHSRV_V1, template, drafted_by="heuristic", draft_id="dr_t")
    compiled = compile(v2)
    assert compiled.version == 2
    assert [t.id for t in compiled.templates] == ["auth_ok", "session_closed", "logon_failed"]
    result = verify(v2, [s.raw for s in prepared.samples], {"logon_failed"})
    assert result.ok, result.to_dict()
    assert result.tiers == {"1": 8}
    kinds = {row.ocsf_path: row.kind for row in result.provenance}
    assert kinds["status_id"] == "derived" and kinds["user.name"] == "located"


def test_onboarding_t1_becomes_a_new_contract() -> None:
    prepared = build(lines("authsrv_t1_ok.log", 3), template_sig="t_t1")
    template = generalize(prepared, heuristic(prepared))
    yaml_text = new_contract(
        contract_id="authsrv",
        tenant_id="t_maha_power",
        source_id="src_authsrv_01",
        layers=prepared.layers,
        templates=[template],
        timezone="Asia/Kolkata",
        drafted_by="heuristic",
        draft_id="dr_1",
    )
    compiled = compile(yaml_text)
    assert compiled.time["field"] == "syslog.timestamp"
    assert verify(yaml_text, [s.raw for s in prepared.samples], {template.id}).ok


def test_a_wrong_but_real_edit_keeps_provenance() -> None:
    """C4 AC3: swap src and dst. The bytes exist, so provenance holds; review must catch it."""
    prepared = t3()
    good = heuristic(prepared)
    swapped = DraftResponse.model_validate(
        good.dump()
        | {
            "mappings": [
                {"ocsf_path": "src_endpoint.ip", "token": "k8"},
                {"ocsf_path": "dst_endpoint.ip", "token": "k6"},
                *[
                    m.model_dump(exclude_none=True)
                    for m in good.mappings
                    if m.ocsf_path not in ("src_endpoint.ip", "dst_endpoint.ip")
                ],
            ]
        }
    )
    template = generalize(prepared, swapped)
    v2 = add_template(AUTHSRV_V1, template, drafted_by="human", draft_id="dr_t")
    result = verify(v2, [s.raw for s in prepared.samples], {template.id})
    assert result.ok and result.type_issues == []


def test_a_wrong_kind_edit_is_a_type_issue() -> None:
    prepared = t3()
    wrong = DraftResponse.model_validate(
        {
            "class": "authentication",
            "activity": "logon",
            "mappings": [{"ocsf_path": "src_endpoint.ip", "token": "k2"}],
        }
    )
    template = generalize(prepared, wrong)
    v2 = add_template(AUTHSRV_V1, template, drafted_by="human", draft_id="dr_t")
    result = verify(v2, [s.raw for s in prepared.samples], {template.id})
    assert any("is not an IP address" in issue for issue in result.type_issues)


def test_literal_angle_brackets_are_escaped() -> None:
    prepared = build([b"<x> user=bob OK"], template_sig="t_x", layers=[])
    template = generalize(prepared, heuristic(prepared))
    assert template.pattern.startswith("\\<x\\> ")
    compile(
        new_contract(
            contract_id="demo",
            tenant_id="t_demo",
            source_id="src_demo_01",
            layers=[],
            templates=[template],
            timezone="UTC",
            drafted_by="heuristic",
            draft_id="dr_x",
        )
    )
