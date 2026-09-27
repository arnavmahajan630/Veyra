"""Pepper, secret digest, the key card and the demo last-key memory."""

from __future__ import annotations

import hashlib
from pathlib import Path

from control_api.keys import IssuedKey, LastKeyMemory, ensure_pepper, key_card, secret_digest
from control_api.tables import Source


def test_pepper_is_created_once_and_its_id_is_stable(tmp_path: Path) -> None:
    pepper, pepper_id = ensure_pepper(tmp_path / "keys")
    assert len(pepper) == 64 and bytes.fromhex(pepper.decode())
    assert pepper_id == "p_" + hashlib.sha256(pepper).hexdigest()[:8]
    assert ensure_pepper(tmp_path / "keys") == (pepper, pepper_id)


def test_secret_digest_is_sha256_of_pepper_then_secret() -> None:
    assert secret_digest(b"pp", "veyra_x") == hashlib.sha256(b"ppveyra_x").hexdigest()


def test_key_card_for_http_push() -> None:
    source = Source(
        id="src_authsrv_01", tenant_id="t_maha_power", name="Auth", vendor="custom",
        zone="dmz", transport="http_push", created_at="x",
    )  # fmt: skip
    card = key_card(public_host="localhost", key_id="k_ABCDEFGH", secret="veyra_s", source=source)
    assert card["endpoints"] == {
        "hec_url": "http://localhost:8088/services/collector/event",
        "batch_url": "http://localhost:8088/v1/batch",
        "syslog": None,
    }
    assert "Authorization: Splunk veyra_s" in card["curl_example"]
    assert card["curl_example"].startswith("curl -s http://localhost:8088/services/collector/event")


def test_key_card_for_syslog_names_the_listener_port() -> None:
    source = Source(
        id="src_x", tenant_id="t_a", name="X", vendor="linux", zone="core",
        transport="syslog_udp", listener="core-udp", created_at="x",
    )  # fmt: skip
    card = key_card(public_host="veyra.local", key_id="k_1", secret="s", source=source)
    syslog = card["endpoints"]["syslog"]
    assert syslog == {"host": "veyra.local", "port": 5524, "listener": "core-udp"}


def test_last_key_memory() -> None:
    memory = LastKeyMemory()
    assert memory.get() is None
    memory.remember(IssuedKey(key_id="k_1", secret="s", source_id="src_a"))
    assert memory.get() == IssuedKey(key_id="k_1", secret="s", source_id="src_a")
    memory.forget()
    assert memory.get() is None
