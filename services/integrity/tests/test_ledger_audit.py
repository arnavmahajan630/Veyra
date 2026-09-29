"""``tools/ledger_audit.py``: PASS on an honest ledger, FAIL on a tampered one."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from integrity.integrity import Integrity
from integrity.settings import IntegritySettings

from veyra_common.envelope import stamp
from veyra_evidence.keys import LocalKeyProvider
from veyra_evidence.segment import SegmentWriter

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "tools" / "ledger_audit.py"
WINDOW = 60


def _load_tool() -> Any:
    """Import tools/ledger_audit.py, which is a script rather than a package module."""
    spec = importlib.util.spec_from_file_location("ledger_audit", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations through sys.modules, so register before exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_tool = _load_tool()


@pytest.fixture
def cfg(tmp_path: Path) -> IntegritySettings:
    return IntegritySettings(
        _env_file=None,
        data_dir=tmp_path,
        merkle_window_seconds=WINDOW,
        integrity_window_lag_seconds=0,
        metrics_port=0,
    )


@pytest.fixture
def keys(cfg: IntegritySettings) -> LocalKeyProvider:
    return LocalKeyProvider(cfg=cfg)


@pytest.fixture
def signed(cfg: IntegritySettings, keys: LocalKeyProvider) -> Integrity:
    """A vault with three segments and three chained, signed roots."""
    service = Integrity(cfg, keys)
    for i in range(3):
        writer = SegmentWriter(
            "raw.custom", i, i * 100, key_provider=keys, vault_dir=cfg.vault_dir, cfg=cfg
        )
        env = stamp(
            f"<134>Sep 26 14:05:11 fw01 app[233]: e{i}".encode(),
            collector_id="it",
            transport="syslog_tcp",
            framing_method="newline",
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            vendor="custom",
            zone="dmz",
        )
        writer.append(i * 100, env.model_dump_json().encode(), env.raw_sha256, env.event_uid)
        writer.seal()
    members = sorted(service.scan_segments(), key=lambda s: s.segment_id)
    for i, member in enumerate(members):
        service.sign_window(1790000000 + i * WINDOW, [member])
    return service


def public_pem(cfg: IntegritySettings, keys: LocalKeyProvider) -> Path:
    path = Path(cfg.data_dir) / "pub.pem"
    path.write_text(keys.public_key_pem(keys.signing_key_id))
    return path


def run_tool(ledger: Path, pubkey: Path) -> tuple[int, dict[str, Any]]:
    """Run the tool as a user would, and parse its JSON report."""
    result = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--ledger",
            str(ledger),
            "--pubkey",
            str(pubkey),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO,
    )
    return result.returncode, json.loads(result.stdout)


def rewrite_entry(ledger: Path, index: int, mutate: Any) -> None:
    lines = ledger.read_text().splitlines()
    entry = json.loads(lines[index])
    mutate(entry)
    lines[index] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    ledger.write_text("\n".join(lines) + "\n")


def test_an_honest_ledger_passes(
    signed: Integrity, cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    code, report = run_tool(signed.ledger, public_pem(cfg, keys))
    assert report["status"] == "PASS", report["findings"]
    assert report["entries"] == 3
    assert report["failures"] == 0
    assert code == 0


def test_modifying_an_old_entry_is_detected(
    signed: Integrity, cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    """The headline check: rewrite entry 0 and the audit must fail."""

    def forge(entry: dict[str, Any]) -> None:
        entry["payload"]["root"] = "0" * 64

    rewrite_entry(signed.ledger, 0, forge)
    code, report = run_tool(signed.ledger, public_pem(cfg, keys))
    assert code == 1
    assert report["status"] == "FAIL"
    failed = {(f["entry"], f["check"]) for f in report["findings"] if f["status"] == "FAIL"}
    # Its own signature no longer covers it, and entry 1's chain link now dangles.
    assert (0, "signature") in failed
    assert (1, "prev_signed_sha256") in failed


def test_a_forged_signature_is_detected(
    signed: Integrity, cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    def forge(entry: dict[str, Any]) -> None:
        entry["sig_b64"] = "A" * 88

    rewrite_entry(signed.ledger, 1, forge)
    code, report = run_tool(signed.ledger, public_pem(cfg, keys))
    assert code == 1
    failed = {(f["entry"], f["check"]) for f in report["findings"] if f["status"] == "FAIL"}
    assert (1, "signature") in failed


def test_a_broken_chain_link_is_detected(
    signed: Integrity, cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    """Only prev_signed_sha256 is edited, so this is purely the chain check."""

    def forge(entry: dict[str, Any]) -> None:
        entry["payload"]["prev_signed_sha256"] = "1" * 64

    rewrite_entry(signed.ledger, 2, forge)
    code, report = run_tool(signed.ledger, public_pem(cfg, keys))
    assert code == 1
    failed = {(f["entry"], f["check"]) for f in report["findings"] if f["status"] == "FAIL"}
    assert (2, "prev_signed_sha256") in failed


def test_deleting_an_entry_is_detected(
    signed: Integrity, cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    """Dropping a root must not go unnoticed, even though the rest still verifies."""
    lines = signed.ledger.read_text().splitlines()
    signed.ledger.write_text("\n".join([lines[0], lines[2]]) + "\n")
    code, report = run_tool(signed.ledger, public_pem(cfg, keys))
    assert code == 1
    failed = {(f["entry"], f["check"]) for f in report["findings"] if f["status"] == "FAIL"}
    assert (1, "prev_signed_sha256") in failed


def test_reordering_entries_is_detected(
    signed: Integrity, cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    lines = signed.ledger.read_text().splitlines()
    signed.ledger.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n")
    code, report = run_tool(signed.ledger, public_pem(cfg, keys))
    assert code == 1
    checks = {f["check"] for f in report["findings"] if f["status"] == "FAIL"}
    assert "prev_signed_sha256" in checks or "window_order" in checks


def test_another_keys_ledger_fails_verification(
    signed: Integrity, cfg: IntegritySettings, tmp_path: Path
) -> None:
    """A ledger signed elsewhere must not verify under this deployment's key."""
    stranger = LocalKeyProvider(keys_dir=tmp_path / "stranger", cfg=cfg)
    path = tmp_path / "stranger.pem"
    path.write_text(stranger.public_key_pem(stranger.signing_key_id))
    code, report = run_tool(signed.ledger, path)
    assert code == 1
    assert all(f["status"] == "FAIL" for f in report["findings"] if f["check"] == "signature")


def test_an_empty_ledger_passes_with_nothing_to_check(
    cfg: IntegritySettings, keys: LocalKeyProvider, tmp_path: Path
) -> None:
    empty = tmp_path / "empty.ndjson"
    empty.write_text("")
    code, report = run_tool(empty, public_pem(cfg, keys))
    assert code == 0
    assert report["entries"] == 0
    assert report["status"] == "PASS"


def test_the_audit_function_works_in_process(
    signed: Integrity, cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    """The evidence API (B4) will call audit() directly rather than the CLI."""
    from veyra_evidence.ledger import read_ledger

    entries = read_ledger(signed.ledger)
    pem = keys.public_key_pem(keys.signing_key_id)
    report = audit_tool.audit(entries, pem, str(signed.ledger))
    assert report.status == "PASS"
    assert report.entries == 3
    assert not report.failures
