"""B4 prototype: the evidence API over a real B2 vault and a real B3 ledger.

The fixtures build the evidence with the actual archiver and integrity code, so what the
API serves is what those phases produce — no Kafka, no ClickHouse, no containers.
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from evidence_api.app import create_app
from evidence_api.export import EXPORT_FILES
from evidence_api.settings import EvidenceApiSettings
from fastapi.testclient import TestClient
from integrity.integrity import Integrity

from veyra_common.envelope import stamp
from veyra_common.models import Envelope
from veyra_evidence.keys import LocalKeyProvider
from veyra_evidence.segment import SegmentWriter

WINDOW = 60
TOPIC = "raw.custom"
EVENTS_PER_SEGMENT = 3
SEGMENTS = 3

PAYLOADS = [
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)',
    "<134>Sep 26 14:05:12 fw01 app[233]: उपयोगकर्ता लॉगिन विफल".encode(),
    b"<134>binary tail: " + bytes(range(256)),
]


@pytest.fixture
def cfg(tmp_path: Path) -> EvidenceApiSettings:
    return EvidenceApiSettings(
        _env_file=None,
        data_dir=tmp_path,
        merkle_window_seconds=WINDOW,
        metrics_port=0,
    )


@pytest.fixture
def keys(cfg: EvidenceApiSettings) -> LocalKeyProvider:
    return LocalKeyProvider(cfg=cfg)


@pytest.fixture
def vault(cfg: EvidenceApiSettings, keys: LocalKeyProvider) -> list[Envelope]:
    """Three sealed segments, all covered by signed window roots."""
    envelopes: list[Envelope] = []
    for partition in range(SEGMENTS):
        writer = SegmentWriter(
            TOPIC,
            partition,
            partition * 100,
            key_provider=keys,
            vault_dir=cfg.vault_dir,
            cfg=cfg,
        )
        for i in range(EVENTS_PER_SEGMENT):
            env = stamp(
                PAYLOADS[i % len(PAYLOADS)] + f" p={partition} i={i}".encode(),
                collector_id="edge-dmz-01",
                transport="syslog_tcp",
                framing_method="newline",
                source_id="src_authsrv_01",
                tenant_id="t_maha_power",
                vendor="custom",
                zone="dmz",
                peer_ip="172.20.0.21",
            )
            writer.append(
                partition * 100 + i, env.model_dump_json().encode(), env.raw_sha256, env.event_uid
            )
            envelopes.append(env)
        writer.seal()

    # Sign every segment's window, the way the integrity service would once closed.
    integrity_cfg = cfg.model_copy(update={"integrity_window_lag_seconds": 0})
    service = Integrity(integrity_cfg, keys)  # type: ignore[arg-type]
    members = sorted(service.scan_segments(), key=lambda s: s.segment_id)
    # Two segments share a window, one sits alone: exercises a multi-leaf tree and a
    # single-leaf tree in the same vault.
    service.sign_window(1790000000, members[:2])
    service.sign_window(1790000000 + WINDOW, members[2:])
    return envelopes


@pytest.fixture
def client(cfg: EvidenceApiSettings, keys: LocalKeyProvider, vault: list[Envelope]) -> TestClient:
    return TestClient(create_app(cfg, keys))


def steps_of(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["id"]: step for step in body["steps"]}


# ---------------------------------------------------------------- health
def test_health_reports_the_vault(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["vault_exists"] is True
    assert body["segments"] == SEGMENTS
    assert body["signed_roots"] == 2


def test_health_works_without_a_vault(cfg: EvidenceApiSettings, keys: LocalKeyProvider) -> None:
    """The service must start and answer before any evidence exists."""
    response = TestClient(create_app(cfg, keys)).get("/health")
    assert response.status_code == 200
    assert response.json()["segments"] == 0


# ---------------------------------------------------------------- lookup
def test_event_lookup_returns_location_and_hash(client: TestClient, vault: list[Envelope]) -> None:
    env = vault[0]
    body = client.get(f"/evidence/{env.event_uid}").json()
    assert body["event_uid"] == env.event_uid
    assert body["raw_sha256"] == env.raw_sha256
    assert body["raw_len"] == env.raw_len
    assert body["source_id"] == "src_authsrv_01"
    assert body["raw_ref"] == {"topic": TOPIC, "partition": 0, "offset": 0}
    assert body["vault"]["segment_id"] == f"seg_{TOPIC}_0_000000000000"
    assert body["vault"]["record_idx"] == 0
    assert len(body["vault"]["chain_hash"]) == 64
    assert body["signed_root"]["root"]


def test_every_archived_event_is_findable(client: TestClient, vault: list[Envelope]) -> None:
    for env in vault:
        body = client.get(f"/evidence/{env.event_uid}").json()
        assert body["raw_sha256"] == env.raw_sha256


def test_raw_bytes_come_back_from_the_sealed_segment(
    client: TestClient, vault: list[Envelope]
) -> None:
    """Including the payload that is not valid UTF-8."""
    binary = next(e for e in vault if b"binary tail" in e.raw_bytes)
    body = client.get(f"/evidence/{binary.event_uid}").json()
    assert body["raw_sha256"] == binary.raw_sha256
    export = client.post(f"/evidence/export/{binary.event_uid}")
    with zipfile.ZipFile(io.BytesIO(export.content)) as zf:
        assert zf.read("raw.bin") == binary.raw_bytes


def test_an_unknown_event_is_404(client: TestClient) -> None:
    response = client.get("/evidence/0192a4f0-0000-7000-8000-00000000dead")
    assert response.status_code == 404


def test_a_malformed_uid_is_400(client: TestClient) -> None:
    assert client.get("/evidence/not-a-uuid").status_code == 400


# ---------------------------------------------------------------- verify
def test_verification_passes_every_implemented_step(
    client: TestClient, vault: list[Envelope]
) -> None:
    body = client.get(f"/evidence/{vault[0].event_uid}/verify").json()
    steps = steps_of(body)
    assert list(steps) == [
        "fetch_raw",
        "decrypt_segment",
        "hash_raw",
        "chain_walk",
        "segment_digest",
        "merkle_inclusion",
        "root_signature",
        "immudb_verified",
    ]
    for step_id in list(steps)[:-1]:
        assert steps[step_id]["ok"] is True, f"{step_id}: {steps[step_id]['detail']}"
        assert steps[step_id]["ms"] >= 0
        assert steps[step_id]["label"]
    # immudb is out of scope for the prototype and must say so plainly.
    assert steps["immudb_verified"]["status"] == "not_implemented"
    assert steps["immudb_verified"]["ok"] is False
    # ...and must not drag the overall verdict down.
    assert body["verified"] is True


def test_the_contract_spelling_of_verify_also_works(
    client: TestClient, vault: list[Envelope]
) -> None:
    uid = vault[0].event_uid

    def without_timings(body: dict[str, Any]) -> dict[str, Any]:
        # `ms` is wall-clock and differs between two identical calls.
        return {
            **body,
            "steps": [{k: v for k, v in s.items() if k != "ms"} for s in body["steps"]],
        }

    alias = without_timings(client.get(f"/evidence/verify/{uid}").json())
    assert alias == without_timings(client.get(f"/evidence/{uid}/verify").json())


def test_verification_of_every_event_in_a_multi_leaf_window(
    client: TestClient, vault: list[Envelope]
) -> None:
    for env in vault:
        body = client.get(f"/evidence/{env.event_uid}/verify").json()
        assert body["verified"] is True, body["steps"]


def test_a_missing_event_fails_every_step_explicitly(client: TestClient) -> None:
    body = client.get("/evidence/0192a4f0-0000-7000-8000-00000000dead/verify").json()
    assert body["verified"] is False
    steps = steps_of(body)
    assert len(steps) == 8
    assert steps["fetch_raw"]["ok"] is False
    assert "not checked" in steps["chain_walk"]["detail"]


# ---------------------------------------------------------------- tampering
def _chmod_writable(path: Path) -> None:
    os.chmod(path, 0o644)


def test_tampered_raw_data_fails_verification(
    client: TestClient, cfg: EvidenceApiSettings, vault: list[Envelope]
) -> None:
    """Flip a byte of a sealed segment: GCM catches it before anything else runs."""
    segment = next(Path(cfg.vault_dir).glob(f"{TOPIC}/0/*.seg"))
    blob = bytearray(segment.read_bytes())
    blob[-1] ^= 0x01
    _chmod_writable(segment)
    segment.write_bytes(bytes(blob))

    body = client.get(f"/evidence/{vault[0].event_uid}/verify").json()
    assert body["verified"] is False
    # The event can no longer even be read out of the segment.
    assert steps_of(body)["fetch_raw"]["ok"] is False


def test_a_tampered_raw_hash_fails_hash_raw(
    cfg: EvidenceApiSettings, keys: LocalKeyProvider
) -> None:
    """An envelope whose raw_sha256 does not match its bytes must fail ``hash_raw``.

    Built directly, because the archiver would never produce this: it is what a forged
    or corrupted envelope looks like.
    """
    writer = SegmentWriter(TOPIC, 7, 0, key_provider=keys, vault_dir=cfg.vault_dir, cfg=cfg)
    env = stamp(
        b"<134>Sep 26 14:05:11 fw01 app[233]: honest line",
        collector_id="it",
        transport="syslog_tcp",
        framing_method="newline",
        source_id="src_authsrv_01",
        tenant_id="t_maha_power",
        vendor="custom",
        zone="dmz",
    )
    forged = env.model_dump(mode="json")
    forged["raw_b64"] = base64.b64encode(b"different bytes entirely").decode()
    writer.append(0, json.dumps(forged).encode(), env.raw_sha256, env.event_uid)
    writer.seal()

    integrity_cfg = cfg.model_copy(update={"integrity_window_lag_seconds": 0})
    service = Integrity(integrity_cfg, keys)  # type: ignore[arg-type]
    members = [s for s in service.scan_segments() if "_7_" in s.segment_id]
    service.sign_window(1790000000, members)

    client = TestClient(create_app(cfg, keys))
    body = client.get(f"/evidence/{env.event_uid}/verify").json()
    steps = steps_of(body)
    assert steps["hash_raw"]["ok"] is False
    assert body["verified"] is False
    # The segment itself is intact, so the chain and the root still check out.
    assert steps["decrypt_segment"]["ok"] is True
    assert steps["root_signature"]["ok"] is True


def test_a_tampered_signed_root_fails_verification(
    client: TestClient, cfg: EvidenceApiSettings, keys: LocalKeyProvider, vault: list[Envelope]
) -> None:
    """Rewrite the root in the ledger: the signature no longer covers it."""
    from veyra_evidence.ledger import ledger_path

    ledger = ledger_path(cfg)
    lines = ledger.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["payload"]["root"] = "0" * 64
    lines[0] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    ledger.write_text("\n".join(lines) + "\n")

    body = TestClient(create_app(cfg, keys)).get(f"/evidence/{vault[0].event_uid}/verify").json()
    steps = steps_of(body)
    assert body["verified"] is False
    assert steps["root_signature"]["ok"] is False
    # The segment is untouched, so its own integrity still passes.
    assert steps["decrypt_segment"]["ok"] is True
    assert steps["chain_walk"]["ok"] is True
    # The forged root is not the one the segment's leaf belongs under.
    assert steps["merkle_inclusion"]["ok"] is False


def test_an_unsigned_window_is_reported_not_silently_passed(
    cfg: EvidenceApiSettings, keys: LocalKeyProvider
) -> None:
    """A segment sealed but not yet signed: the vault checks pass, the root ones do not."""
    writer = SegmentWriter(TOPIC, 8, 0, key_provider=keys, vault_dir=cfg.vault_dir, cfg=cfg)
    env = stamp(
        b"<134>Sep 26 14:05:11 fw01 app[233]: not signed yet",
        collector_id="it",
        transport="syslog_tcp",
        framing_method="newline",
        source_id="src_authsrv_01",
        tenant_id="t_maha_power",
        vendor="custom",
        zone="dmz",
    )
    writer.append(0, env.model_dump_json().encode(), env.raw_sha256, env.event_uid)
    writer.seal()

    body = TestClient(create_app(cfg, keys)).get(f"/evidence/{env.event_uid}/verify").json()
    steps = steps_of(body)
    assert steps["chain_walk"]["ok"] is True
    assert steps["merkle_inclusion"]["ok"] is False
    assert "not been signed" in steps["merkle_inclusion"]["detail"]
    assert body["verified"] is False


# ---------------------------------------------------------------- roots / pubkey
def test_roots_lists_the_ledger(client: TestClient) -> None:
    body = client.get("/evidence/roots").json()
    assert body["count"] == 2
    assert len(body["roots"]) == 2
    first = body["roots"][0]
    assert first["payload"]["alg"] == "Ed25519"
    assert len(first["payload"]["root"]) == 64
    assert first["sig_b64"]
    assert len(first["payload_sha256"]) == 64
    # Newest first, so the windows descend.
    assert body["roots"][0]["payload"]["window_start"] > body["roots"][1]["payload"]["window_start"]


def test_roots_is_empty_before_anything_is_signed(
    cfg: EvidenceApiSettings, keys: LocalKeyProvider
) -> None:
    body = TestClient(create_app(cfg, keys)).get("/evidence/roots").json()
    assert body["count"] == 0
    assert body["roots"] == []


def test_pubkey_returns_the_signing_key(client: TestClient, keys: LocalKeyProvider) -> None:
    response = client.get("/evidence/pubkey")
    assert response.status_code == 200
    assert "BEGIN PUBLIC KEY" in response.text
    assert response.text.strip() == keys.public_key_pem(keys.signing_key_id).strip()


# ---------------------------------------------------------------- export
def test_export_contains_every_expected_file(client: TestClient, vault: list[Envelope]) -> None:
    env = vault[0]
    response = client.post(f"/evidence/export/{env.event_uid}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert env.event_uid in response.headers["content-disposition"]
    assert response.headers["X-Veyra-Verified"] == "true"

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert set(EXPORT_FILES) <= set(zf.namelist())
        assert zf.read("raw.bin") == env.raw_bytes
        envelope = json.loads(zf.read("envelope.json"))
        assert envelope["event_uid"] == env.event_uid
        assert envelope["raw_sha256"] == env.raw_sha256
        manifest = json.loads(zf.read("segment_manifest.json"))
        assert manifest["header"]["segment_id"] == f"seg_{TOPIC}_0_000000000000"
        assert len(manifest["records"]) == EVENTS_PER_SEGMENT
        signed_root = json.loads(zf.read("signed_root.json"))
        assert signed_root["payload"]["alg"] == "Ed25519"
        assert "BEGIN PUBLIC KEY" in zf.read("pubkey.pem").decode()
        # The proof is non-empty for a multi-leaf window.
        assert json.loads(zf.read("proof.json"))


def test_the_export_carries_no_key_material(client: TestClient, vault: list[Envelope]) -> None:
    """The bundle must be safe to hand over: no DEK, no private key."""
    response = client.post(f"/evidence/export/{vault[0].event_uid}")
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        manifest = zf.read("segment_manifest.json").decode()
        assert "wrapped_dek_b64" not in manifest
        assert "nonce_b64" not in manifest
        assert "PRIVATE KEY" not in zf.read("pubkey.pem").decode()
        # ...but what verification does need is still there.
        header = json.loads(manifest)["header"]
        for field in ("segment_id", "prev_chain_hash_hex", "last_chain_hash_hex", "record_count"):
            assert field in header


def test_exporting_an_unknown_event_is_404(client: TestClient) -> None:
    assert client.post("/evidence/export/0192a4f0-0000-7000-8000-00000000dead").status_code == 404


def _run_bundled_verify(content: bytes, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    out = tmp_path / "bundle"
    out.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        zf.extractall(out)
    return subprocess.run(
        [sys.executable, "verify.py"], cwd=out, capture_output=True, text=True, timeout=120
    )


def test_the_bundled_verify_script_passes_on_honest_evidence(
    client: TestClient, vault: list[Envelope], tmp_path: Path
) -> None:
    """The auditor's path: unzip, run verify.py, get PASS — no VEYRA install involved."""
    response = client.post(f"/evidence/export/{vault[0].event_uid}")
    result = _run_bundled_verify(response.content, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    for check in ("hash_raw", "chain_walk", "segment_digest", "merkle_inclusion", "root_signature"):
        assert f"PASS  {check}" in result.stdout, result.stdout
    assert "FAIL" not in result.stdout


def test_the_bundled_verify_script_works_for_a_single_leaf_window(
    client: TestClient, vault: list[Envelope], tmp_path: Path
) -> None:
    """The third segment is alone in its window, so its proof is empty."""
    env = vault[-1]
    result = _run_bundled_verify(client.post(f"/evidence/export/{env.event_uid}").content, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_bundled_verify_script_detects_tampered_raw_bytes(
    client: TestClient, vault: list[Envelope], tmp_path: Path
) -> None:
    """Edit raw.bin inside the bundle and the script must refuse it."""
    response = client.post(f"/evidence/export/{vault[0].event_uid}")
    out = tmp_path / "tampered"
    out.mkdir()
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        zf.extractall(out)
    (out / "raw.bin").write_bytes(b"this is not what was archived")
    result = subprocess.run(
        [sys.executable, "verify.py"], cwd=out, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 1
    assert "FAIL  hash_raw" in result.stdout


def test_the_bundled_verify_script_detects_a_tampered_root(
    client: TestClient, vault: list[Envelope], tmp_path: Path
) -> None:
    response = client.post(f"/evidence/export/{vault[0].event_uid}")
    out = tmp_path / "forged-root"
    out.mkdir()
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        zf.extractall(out)
    signed = json.loads((out / "signed_root.json").read_text())
    signed["payload"]["root"] = "0" * 64
    (out / "signed_root.json").write_text(json.dumps(signed, indent=2))
    result = subprocess.run(
        [sys.executable, "verify.py"], cwd=out, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 1
    assert "FAIL  merkle_inclusion" in result.stdout
    assert "FAIL  root_signature" in result.stdout


# ---------------------------------------------------------------- bundled Ed25519
def _verify_script_namespace() -> dict[str, Any]:
    """Exec the bundled verify.py so its helpers can be tested directly."""
    from evidence_api.verify_template import VERIFY_PY

    namespace: dict[str, Any] = {"__name__": "bundled_verify"}
    exec(compile(VERIFY_PY, "verify.py", "exec"), namespace)
    return namespace


def test_the_bundled_ed25519_accepts_a_real_signature(keys: LocalKeyProvider) -> None:
    """The dependency-free path must agree with `cryptography`, not just exist."""
    namespace = _verify_script_namespace()
    payload = b'{"root":"deadbeef","window_id":"w_1790000000"}'
    signature = keys.sign(keys.signing_key_id, payload)
    pem = keys.public_key_pem(keys.signing_key_id)
    assert namespace["ed25519_verify"](pem, payload, signature) is True


def test_the_bundled_ed25519_rejects_a_bad_signature(keys: LocalKeyProvider) -> None:
    namespace = _verify_script_namespace()
    pem = keys.public_key_pem(keys.signing_key_id)
    payload = b"the signed bytes"
    signature = keys.sign(keys.signing_key_id, payload)
    verify = namespace["ed25519_verify"]
    assert verify(pem, b"different bytes", signature) is False
    flipped = bytearray(signature)
    flipped[-1] ^= 0x01
    assert verify(pem, payload, bytes(flipped)) is False
    assert verify(pem, payload, signature[:32]) is False


def test_the_bundled_ed25519_rejects_another_keys_signature(
    keys: LocalKeyProvider, cfg: EvidenceApiSettings, tmp_path: Path
) -> None:
    namespace = _verify_script_namespace()
    stranger = LocalKeyProvider(keys_dir=tmp_path / "stranger", cfg=cfg)
    payload = b"signed by someone else"
    signature = stranger.sign(stranger.signing_key_id, payload)
    mine = keys.public_key_pem(keys.signing_key_id)
    assert namespace["ed25519_verify"](mine, payload, signature) is False
