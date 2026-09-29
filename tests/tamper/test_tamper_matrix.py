"""B5 tamper lab: each mode breaks the evidence, B4 notices, untamper puts it back.

One run per mode (a matrix, not a fuzz campaign). The vault and ledger are built with the
real B2/B3 code, so what is being tampered with is genuine evidence.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from integrity.integrity import Integrity
from integrity.settings import IntegritySettings

from veyra_common.envelope import stamp
from veyra_common.models import Envelope
from veyra_evidence.keys import LocalKeyProvider
from veyra_evidence.ledger import ledger_path
from veyra_evidence.segment import SegmentWriter

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "tamper.py"
WINDOW = 60
TOPIC = "raw.custom"


def _load_tool() -> Any:
    spec = importlib.util.spec_from_file_location("tamper", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tamper_tool = _load_tool()

# The first failing check each mode is expected to trigger (docs/tamper_matrix.md).
EXPECTED_STEP = {
    "naive_flip": "fetch_raw",
    "insider_rewrite": "merkle_inclusion",
    "segment_delete": "fetch_raw",
    "root_rewrite": "root_signature",
}


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
def evidence(cfg: IntegritySettings, keys: LocalKeyProvider) -> list[Envelope]:
    """Two sealed segments in one signed window, so proofs have real siblings."""
    envelopes: list[Envelope] = []
    for partition in range(2):
        writer = SegmentWriter(
            TOPIC,
            partition,
            partition * 100,
            key_provider=keys,
            vault_dir=cfg.vault_dir,
            cfg=cfg,
        )
        for i in range(2):
            env = stamp(
                f"<134>Sep 26 14:05:11 fw01 app[233]: user=a.sharma p={partition} i={i}".encode(),
                collector_id="edge-dmz-01",
                transport="syslog_tcp",
                framing_method="newline",
                source_id="src_authsrv_01",
                tenant_id="t_maha_power",
                vendor="custom",
                zone="dmz",
            )
            writer.append(
                partition * 100 + i, env.model_dump_json().encode(), env.raw_sha256, env.event_uid
            )
            envelopes.append(env)
        writer.seal()
    service = Integrity(cfg, keys)
    service.sign_window(1790000000, sorted(service.scan_segments(), key=lambda s: s.segment_id))
    return envelopes


@pytest.fixture
def lab(cfg: IntegritySettings, keys: LocalKeyProvider, evidence: list[Envelope]) -> Any:
    return tamper_tool.open_lab(cfg)


def snapshot(cfg: IntegritySettings) -> dict[str, bytes]:
    """Every vault file plus the ledger, so a restore can be compared byte-for-byte."""
    files = {
        str(p.relative_to(cfg.data_dir)): p.read_bytes()
        for p in sorted(Path(cfg.vault_dir).rglob("*"))
        if p.is_file()
    }
    ledger = ledger_path(cfg)
    if ledger.exists():
        files["ledger"] = ledger.read_bytes()
    return files


# ---------------------------------------------------------------- the matrix
@pytest.mark.parametrize("mode", sorted(EXPECTED_STEP))
def test_each_mode_is_detected_and_reversible(
    mode: str, lab: Any, cfg: IntegritySettings, evidence: list[Envelope]
) -> None:
    uid = evidence[0].event_uid
    before = snapshot(cfg)
    assert lab.verifier.verify(uid).verified is True, "the evidence should start sound"

    result = tamper_tool.tamper(lab, mode, uid)

    # 1. the evidence no longer verifies
    assert result["verified"] is False, result["report"]
    # 2. the expected check is the one that caught it
    assert EXPECTED_STEP[mode] in result["failed_steps"], result["failed_steps"]
    # 3. something actually changed on disk
    assert snapshot(cfg) != before
    # 4. a backup exists for every file the mode touched
    entries = tamper_tool._read_manifest(lab)
    assert entries and entries[-1]["mode"] == mode
    for item in entries[-1]["files"]:
        assert item["existed"] is True
        assert Path(item["backup"]).is_file()

    # 5. untamper restores the vault exactly
    tamper_tool.untamper(lab, uid)
    assert snapshot(cfg) == before
    assert lab.verifier.verify(uid).verified is True


def test_a_backup_is_made_before_anything_changes(
    lab: Any, cfg: IntegritySettings, evidence: list[Envelope]
) -> None:
    """The backup must hold the *original* bytes, not the tampered ones."""
    uid = evidence[0].event_uid
    original = lab.locator.locate(uid).path.read_bytes()
    tamper_tool.tamper(lab, "naive_flip", uid)
    backup = Path(tamper_tool._read_manifest(lab)[-1]["files"][0]["backup"])
    assert backup.read_bytes() == original


def test_insider_rewrite_keeps_the_segment_internally_consistent(
    lab: Any, evidence: list[Envelope]
) -> None:
    """The attacker with the KEK produces a segment that passes every *local* check.

    Only the signed Merkle root, which they cannot forge, gives them away.
    """
    uid = evidence[0].event_uid
    result = tamper_tool.tamper(lab, "insider_rewrite", uid)
    steps = {s["id"]: s for s in result["report"]["steps"]}
    for local_check in ("fetch_raw", "decrypt_segment", "hash_raw", "chain_walk", "segment_digest"):
        assert steps[local_check]["ok"] is True, f"{local_check}: {steps[local_check]['detail']}"
    assert steps["merkle_inclusion"]["ok"] is False
    assert result["failed_steps"] == ["merkle_inclusion"]
    # The altered payload really is in the vault now.
    located = lab.locator.locate(uid)
    import base64

    assert b"ALTERED BY AN INSIDER" in base64.b64decode(located.envelope["raw_b64"])


def test_segment_delete_removes_the_file_and_untamper_brings_it_back(
    lab: Any, evidence: list[Envelope]
) -> None:
    uid = evidence[0].event_uid
    path = lab.locator.locate(uid).path
    tamper_tool.tamper(lab, "segment_delete", uid)
    assert not path.exists()
    tamper_tool.untamper(lab, uid)
    assert path.exists()
    assert lab.verifier.verify(uid).verified is True


def test_root_rewrite_leaves_the_segment_alone(lab: Any, evidence: list[Envelope]) -> None:
    """Only the ledger is touched, so the vault's own checks still pass."""
    uid = evidence[0].event_uid
    result = tamper_tool.tamper(lab, "root_rewrite", uid)
    steps = {s["id"]: s for s in result["report"]["steps"]}
    assert steps["decrypt_segment"]["ok"] is True
    assert steps["chain_walk"]["ok"] is True
    assert steps["root_signature"]["ok"] is False
    assert "merkle_inclusion" in result["failed_steps"]


def test_untamper_is_safe_to_run_twice(
    lab: Any, cfg: IntegritySettings, evidence: list[Envelope]
) -> None:
    uid = evidence[0].event_uid
    before = snapshot(cfg)
    tamper_tool.tamper(lab, "naive_flip", uid)
    tamper_tool.untamper(lab, uid)
    assert tamper_tool.untamper(lab, uid)["operations_undone"] == 0
    assert snapshot(cfg) == before


def test_untamper_without_an_event_undoes_everything(
    lab: Any, cfg: IntegritySettings, evidence: list[Envelope]
) -> None:
    before = snapshot(cfg)
    tamper_tool.tamper(lab, "root_rewrite", evidence[0].event_uid)
    tamper_tool.tamper(lab, "naive_flip", evidence[-1].event_uid)
    assert len(tamper_tool.active(lab)) == 2
    tamper_tool.untamper(lab)
    assert tamper_tool.active(lab) == []
    assert snapshot(cfg) == before


def test_an_unknown_event_is_refused(lab: Any) -> None:
    with pytest.raises(tamper_tool.TamperError, match="no sealed segment"):
        tamper_tool.tamper(lab, "naive_flip", "0192a4f0-0000-7000-8000-00000000dead")


def test_an_unknown_mode_is_refused(lab: Any, evidence: list[Envelope]) -> None:
    with pytest.raises(tamper_tool.TamperError, match="unknown mode"):
        tamper_tool.tamper(lab, "nuke_everything", evidence[0].event_uid)


def test_the_cli_runs_end_to_end(
    cfg: IntegritySettings, evidence: list[Envelope], monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the demo operator actually types."""
    monkeypatch.setenv("VEYRA_DATA_DIR", str(cfg.data_dir))
    uid = evidence[0].event_uid
    import subprocess

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=180,
        )

    assert run("verify", "--event", uid).returncode == 0

    tampered = run("naive_flip", "--event", uid, "--json")
    assert tampered.returncode == 0, tampered.stderr
    body = json.loads(tampered.stdout)
    assert body["verified"] is False
    assert "fetch_raw" in body["failed_steps"]

    assert "naive_flip" in run("list").stdout
    assert run("verify", "--event", uid).returncode == 1  # exits non-zero while broken
    assert run("untamper", "--event", uid).returncode == 0
    assert run("verify", "--event", uid).returncode == 0
    assert "pristine" in run("list").stdout
