"""compile(): IF-CONTRACT-YAML → IF-CONTRACT-COMPILED, deterministic, with located errors."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import re2

from veyra_common.models import ContractMessage
from veyra_contracts import EPOCH, CompiledContract, ContractError, compile
from veyra_engine import Engine

# The contract registry is a separate repository checked out beside Veyra.
_REGISTRY = os.environ.get(
    "VEYRA_CONTRACTS_REPO", Path(__file__).resolve().parents[4] / "contracts-repo"
)
SEED = Path(_REGISTRY).resolve() / "t_ntro_core"
needs_seed = pytest.mark.skipif(
    not SEED.is_dir(), reason=f"needs the contracts repository checked out at {SEED.parent}"
)

AUTHSRV = """\
contract: authsrv
version: 2
tenant: t_maha_power
sources: [src_authsrv_01]
state: draft
envelope:
  - syslog: {variant: auto}
  - json: {text_field: msg}
time:
  field: syslog.timestamp
  formats: ["%b %d %H:%M:%S"]
  timezone: Asia/Kolkata
  year: infer_from_received
templates:
  - id: auth_failed
    pattern: 'user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>'
    class: authentication
    activity: logon
    map:
      user.name: $user
      src_endpoint.ip: $src_ip
      dst_endpoint.ip: $dst_ip
      status_id: {const: 2}
      severity_id: {const: 3}
      device.hostname: $syslog.host
      message: $__text
    unmapped: [attempts]
required: [time, user.name, src_endpoint.ip]
enrich: [asset_inventory, zone_map]
pii: [user.name, src_endpoint.ip]
vocab: [status_words]
"""


def line_of(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


def test_authsrv_compiles_to_if_contract_compiled() -> None:
    compiled = compile(AUTHSRV).to_dict()
    assert compiled["contract"] == "authsrv" and compiled["version"] == 2
    assert compiled["compiled_at"] == EPOCH
    assert compiled["compiler_version"] == "0.1.0"
    template = compiled["templates"][0]
    assert (template["class_uid"], template["activity_id"], template["type_uid"]) == (
        3002,
        1,
        300201,
    )
    assert template["category"] == "iam"
    assert [c["name"] for c in template["captures"]] == ["user", "src_ip", "dst_ip", "attempts"]
    kinds = {e["ocsf_path"]: (e["kind"], e["ref"], e["value"]) for e in template["map"]}
    assert kinds == {
        "user.name": ("capture", "user", None),
        "src_endpoint.ip": ("capture", "src_ip", None),
        "dst_endpoint.ip": ("capture", "dst_ip", None),
        "status_id": ("const", None, 2),
        "severity_id": ("const", None, 3),
        "device.hostname": ("field", "syslog.host", None),
        "message": ("text", "__text", None),
    }
    assert template["unmapped"] == ["attempts"]
    regex = re2.compile(template["regex"])
    assert regex.search("user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1")


@needs_seed
@pytest.mark.parametrize("name", ["linux_sshd.yaml", "acme_ngfw_cef.yaml"])
def test_the_seed_library_contracts_compile(name: str) -> None:
    compiled = compile((SEED / name).read_text(encoding="utf-8"))
    assert compiled.templates


@needs_seed
def test_sshd_template_matches_a_real_line() -> None:
    compiled = compile((SEED / "linux_sshd.yaml").read_text(encoding="utf-8"))
    regex = re2.compile(compiled.templates[0].regex)
    found = regex.search("Failed password for root from 45.12.3.9 port 22 ssh2")
    assert found is not None
    assert found.groupdict() == {"user": "root", "src_ip": "45.12.3.9", "src_port": "22"}


def test_output_is_byte_identical_across_runs_and_processes() -> None:
    first = compile(AUTHSRV).to_json()
    assert compile(AUTHSRV).to_json() == first
    code = (
        "import hashlib,sys; from veyra_contracts import compile;"
        "print(hashlib.sha256(compile(sys.stdin.read()).to_json()).hexdigest())"
    )
    env = {**os.environ, "PYTHONHASHSEED": "12345"}
    out = subprocess.run(
        [sys.executable, "-c", code], input=AUTHSRV, capture_output=True, text=True,
        env=env, check=True,
    )  # fmt: skip
    assert out.stdout.strip() == hashlib.sha256(first).hexdigest()


def test_compiled_at_is_the_only_input_besides_the_yaml() -> None:
    compiled = compile(AUTHSRV, compiled_at="2026-09-27T10:00:00.000000000Z")
    assert compiled.compiled_at == "2026-09-27T10:00:00.000000000Z"


@needs_seed
def test_compiled_contract_feeds_the_engine_and_the_control_message() -> None:
    compiled = compile((SEED / "linux_sshd.yaml").read_text(encoding="utf-8"))
    ContractMessage(
        id=compiled.contract, version=compiled.version, state="active",
        tenant_id=compiled.tenant, sources=compiled.sources, compiled=compiled.to_dict(),
        published_at=EPOCH,
    )  # fmt: skip
    engine = Engine()
    engine.load([compiled.to_dict()])
    found = engine.contract_for_source("src_lnx_core_07")
    assert found is not None and found["contract"] == "linux_sshd"


def test_empty_templates_still_compile() -> None:
    text = "contract: authsrv\nversion: 1\ntenant: t_maha_power\ntemplates: []\n"
    assert compile(text).templates == []


@pytest.mark.parametrize(
    ("old", "new", "message", "needle"),
    [
        ("class: authentication", "class: authentcation", "unknown OCSF class", "class:"),
        ("activity: logon", "activity: login", "unknown activity 'login'", "activity:"),
        ("status_id: {const: 2}", "status_id: {const: 7}", "not a valid status_id", "status_id"),
        ("status_id: {const: 2}", 'status_id: {const: "2"}', "not a valid status_id", "status_id"),
        ("user.name: $user", "user.name: $usr", "not a capture", "user.name: $usr"),
        ("user.name: $user", "user.nmae: $user", "OCSF field catalogue", "user.nmae"),
        ("$syslog.host", "$cef.host", "does not declare", "device.hostname"),
        ("attempts:<attempts:int>'", "attempts:<attempts:float>'", "unknown capture", "pattern:"),
        ("pii: [user.name", "pii: [user.nam", "OCSF field catalogue", "pii:"),
    ],
)
def test_semantic_errors_point_at_the_yaml_line(
    old: str, new: str, message: str, needle: str
) -> None:
    text = AUTHSRV.replace(old, new, 1)
    with pytest.raises(ContractError, match=message) as info:
        compile(text)
    assert info.value.line == line_of(text, needle)


def test_structural_errors_point_at_the_yaml_line() -> None:
    text = AUTHSRV + "colour: red\n"
    with pytest.raises(ContractError, match="colour") as info:
        compile(text)
    assert info.value.line == line_of(text, "colour")


def test_yaml_syntax_errors_are_contract_errors() -> None:
    with pytest.raises(ContractError, match="invalid YAML") as info:
        compile("contract: authsrv\ntemplates: [\n")
    assert info.value.line is not None


def test_a_non_mapping_document_is_rejected() -> None:
    with pytest.raises(ContractError, match="must be a YAML mapping"):
        compile("- just\n- a list\n")


def test_compiled_contract_type_is_exported() -> None:
    assert isinstance(compile(AUTHSRV), CompiledContract)


@pytest.mark.parametrize(
    "new",
    ["status_id: {const: 2.0}", "status_id: {const: true}"],
)
def test_const_enum_rejects_non_exact_type_matches(new: str) -> None:
    text = AUTHSRV.replace("status_id: {const: 2}", new, 1)
    with pytest.raises(ContractError, match="not a valid status_id") as info:
        compile(text)
    assert info.value.line == line_of(text, "status_id")


def test_const_enum_accepts_a_valid_int() -> None:
    compiled = compile(AUTHSRV).to_dict()
    entries = {e["ocsf_path"]: e for e in compiled["templates"][0]["map"]}
    assert entries["status_id"]["kind"] == "const"
    assert entries["status_id"]["value"] == 2


VOCAB_TS = """\
contract: authsrv
version: 1
tenant: t_maha_power
sources: [src_authsrv_01]
templates:
  - id: auth_status
    pattern: 'status=<status_word> at <ts_raw:rest>'
    class: authentication
    activity: logon
    map:
      status_detail: {vocab: status_words, from: $status_word}
      time: {ts: $ts_raw, formats: ["%b %d %H:%M:%S"]}
vocab: [status_words]
"""


def test_vocab_map_entry_compiles() -> None:
    compiled = compile(VOCAB_TS).to_dict()
    entries = {e["ocsf_path"]: e for e in compiled["templates"][0]["map"]}
    entry = entries["status_detail"]
    assert entry["kind"] == "vocab"
    assert entry["ref"] == "status_word"
    assert entry["vocab"] == "status_words"


def test_ts_map_entry_compiles() -> None:
    compiled = compile(VOCAB_TS).to_dict()
    entries = {e["ocsf_path"]: e for e in compiled["templates"][0]["map"]}
    entry = entries["time"]
    assert entry["kind"] == "ts"
    assert entry["ref"] == "ts_raw"
    assert entry["value"] == ["%b %d %H:%M:%S"]


@pytest.mark.parametrize(
    ("old", "new", "message", "needle"),
    [
        (
            "vocab: [status_words]",
            "vocab: []",
            "not in the contract's vocab list",
            "status_detail:",
        ),
        (
            "from: $status_word",
            "from: $missing_word",
            "not a capture of this template",
            "status_detail:",
        ),
        (
            "ts: $ts_raw",
            "ts: $missing_ts",
            "not a capture of this template",
            "time:",
        ),
    ],
)
def test_vocab_and_ts_semantic_errors_point_at_the_yaml_line(
    old: str, new: str, message: str, needle: str
) -> None:
    text = VOCAB_TS.replace(old, new, 1)
    with pytest.raises(ContractError, match=message) as info:
        compile(text)
    assert info.value.line == line_of(text, needle)
