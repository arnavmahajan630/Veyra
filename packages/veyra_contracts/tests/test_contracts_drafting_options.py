"""The fixed answer lists (C4): which fields a token may fill, by the shape of its value."""

from __future__ import annotations

from pathlib import Path

from veyra_common.framing import split_lines
from veyra_contracts.catalogue import CLASSES, ENUMS, FIELDS
from veyra_contracts.drafting.heuristic import heuristic
from veyra_contracts.drafting.options import (
    CLASS_ACTIVITIES,
    CONST_PATHS,
    TEXT_PATHS,
    paths_for,
)
from veyra_contracts.drafting.request import build
from veyra_engine import Token

CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"


def token(value: str, kind: str = "word") -> Token:
    return Token("k1", value, 0, len(value), kind)  # type: ignore[arg-type]


def test_an_address_may_only_fill_address_fields() -> None:
    expected = ("device.ip", "dst_endpoint.ip", "src_endpoint.ip")
    assert paths_for(token("45.12.3.9", "ip")) == expected
    assert paths_for(token("2001:db8::1", "ipv6")) == expected
    # The kind is a guess; an address inside key=value is still an address.
    assert paths_for(token("10.2.3.4", "kv_value")) == expected


def test_a_number_may_fill_ports_and_counts_but_no_text_field() -> None:
    paths = paths_for(token("52144", "int"))
    assert {"src_endpoint.port", "dst_endpoint.port", "process.pid", "user.uid"} <= set(paths)
    assert "user.name" not in paths and "src_endpoint.ip" not in paths


def test_a_number_too_big_for_a_port_is_not_offered_one() -> None:
    paths = paths_for(token("482113", "int"))
    assert not any(path.endswith(".port") for path in paths)
    assert "traffic.bytes_out" in paths


def test_text_may_fill_text_fields_only() -> None:
    paths = paths_for(token("neel.k", "hostname"))
    assert paths == TEXT_PATHS
    assert {"user.name", "actor.user.name", "src_endpoint.hostname"} <= set(paths)
    assert not any(path.endswith((".ip", ".port")) for path in paths)


def test_enum_and_contract_owned_paths_are_never_offered_to_a_token() -> None:
    offered = (
        set(paths_for(token("1.2.3.4"))) | set(paths_for(token("22"))) | set(paths_for(token("x")))
    )
    assert offered.isdisjoint(ENUMS)
    assert offered.isdisjoint({"message", "time", "action_id"})
    assert offered <= FIELDS
    assert tuple(sorted(ENUMS)) == CONST_PATHS


def test_class_activities_are_every_valid_pair() -> None:
    assert len(CLASS_ACTIVITIES) == sum(len(c.activities) for c in CLASSES.values())
    assert ("authentication", "logon") in CLASS_ACTIVITIES
    assert all(activity in CLASSES[name].activities for name, activity in CLASS_ACTIVITIES)


def test_the_lists_never_exclude_a_right_answer_on_the_demo_shapes() -> None:
    """T1-T3: every mapping the reviewed (heuristic) draft makes is among the offered fields."""
    for name in ("authsrv_t1_ok.log", "authsrv_t2_session.log", "authsrv_t3_failed.log"):
        raws = [f.raw for f in split_lines((CORPUS / name).read_bytes())][:8]
        prepared = build(raws, template_sig="t_probe")
        for mapping in heuristic(prepared).mappings:
            if mapping.token is not None:
                assert mapping.ocsf_path in paths_for(prepared.token(mapping.token)), (
                    name,
                    mapping,
                )
