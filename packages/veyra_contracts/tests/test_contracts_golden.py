"""The golden runner: samples through the real engine, diffed against expected files."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from veyra_contracts import compile
from veyra_contracts.golden import (
    GOLDEN_RECEIVED,
    diff_paths,
    run_golden,
    split_header,
)
from veyra_contracts.models import ContractYaml

CONTRACT = """\
contract: demo_auth
version: 1
tenant: t_demo
sources: [src_demo_01]
templates:
  - id: ok
    pattern: 'user=<user> OK login from <src_ip:ip>'
    class: authentication
    activity: logon
    map:
      user.name: $user
      src_endpoint.ip: $src_ip
      status_id: {const: 1}
required: [time, user.name]
pii: [user.name, src_endpoint.ip]
tests:
  - sample: samples/demo_auth/ok_1.log
    expect: expected/demo_auth/ok_1.json
"""


def _setup(root: Path, sample: bytes) -> tuple[ContractYaml, object]:
    (root / "samples" / "demo_auth").mkdir(parents=True)
    (root / "samples" / "demo_auth" / "ok_1.log").write_bytes(sample)
    spec = ContractYaml.model_validate(yaml.safe_load(CONTRACT))
    return spec, compile(CONTRACT)


def test_update_writes_the_expected_file_and_the_next_run_passes(tmp_path: Path) -> None:
    spec, compiled = _setup(tmp_path, b"user=r.patil OK login from 10.4.1.20\n")
    first = run_golden(spec, compiled, tmp_path, update=True)  # type: ignore[arg-type]
    expected = json.loads((tmp_path / "expected/demo_auth/ok_1.json").read_text("utf-8"))
    assert expected["user"]["name"] == "r.patil"
    assert expected["ulpf"]["tier"] == 1
    assert first.passed

    again = run_golden(spec, compiled, tmp_path)  # type: ignore[arg-type]
    assert again.passed and again.total == 1
    assert again.cases[0].tier == 1


def test_a_changed_expected_value_is_reported_as_a_path(tmp_path: Path) -> None:
    spec, compiled = _setup(tmp_path, b"user=r.patil OK login from 10.4.1.20\n")
    run_golden(spec, compiled, tmp_path, update=True)  # type: ignore[arg-type]
    path = tmp_path / "expected/demo_auth/ok_1.json"
    expected = json.loads(path.read_text("utf-8"))
    expected["user"]["name"] = "someone.else"
    path.write_text(json.dumps(expected), encoding="utf-8")

    report = run_golden(spec, compiled, tmp_path)  # type: ignore[arg-type]
    assert not report.passed
    assert "user.name" in report.cases[0].diffs


def test_missing_files_fail_with_a_reason(tmp_path: Path) -> None:
    spec = ContractYaml.model_validate(yaml.safe_load(CONTRACT))
    report = run_golden(spec, compile(CONTRACT), tmp_path)
    assert report.cases[0].error == "sample missing"
    assert report.to_dict()["failed"] == 1


def test_the_header_line_sets_received_time_and_is_not_part_of_the_event() -> None:
    raw, received = split_header(b"#! received_time=2027-01-02T00:00:00.000000000Z\nabc\n")
    assert (raw, received) == (b"abc", "2027-01-02T00:00:00.000000000Z")
    assert split_header(b"abc\r\n") == (b"abc", GOLDEN_RECEIVED)


def test_diff_paths_ignores_the_per_run_fields_and_reports_the_rest() -> None:
    expected = {"a": 1, "ulpf": {"event_uid": "x", "tier": 1}, "l": [1, 2]}
    actual = {"a": 2, "ulpf": {"event_uid": "y", "tier": 1}, "l": [1, 3], "new": True}
    assert diff_paths(expected, actual) == ["a", "l[1]", "new"]
    assert diff_paths({"n": 1}, {"n": 1.0}) == ["n"]
