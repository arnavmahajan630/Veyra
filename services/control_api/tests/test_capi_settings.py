"""C1 knobs exist with laptop defaults, and every profile sets them (P5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from veyra_common.settings import Settings

PROFILES = Path(__file__).resolve().parents[3] / "profiles"
C1_KEYS = [
    "VEYRA_CONTROL_API_PORT",
    "VEYRA_CONTROL_DB",
    "VEYRA_CONTRACTS_REPO",
    "VEYRA_INVENTORY_FILE",
    "VEYRA_INVENTORY_RELOAD_STAMP",
    "VEYRA_SESSION_TTL_MIN",
    "VEYRA_DEMO_PASSWORD",
    "VEYRA_PUBLIC_HOST",
    "VEYRA_CONTROL_PUBLISH_TIMEOUT_S",
    "VEYRA_SSE_HEARTBEAT_S",
    "VEYRA_SSE_QUEUE_MAX",
    "VEYRA_API_PAGE_DEFAULT",
    "VEYRA_API_PAGE_MAX",
]


def test_control_plane_knobs_have_laptop_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VEYRA_CONTRACTS_REPO", raising=False)
    s = Settings(_env_file=None)
    assert s.control_api_port == 8000
    assert s.control_db == Path("data/control/control.db")
    assert s.contracts_repo == Path("../contracts-repo")
    assert s.inventory_file == Path("edge/vector/inventory/sources.csv")
    assert s.inventory_reload_stamp == Path("edge/vector/reload.stamp")
    assert s.session_ttl_min == 480
    assert s.demo_password == "veyra-demo"
    assert s.public_host == "localhost"
    assert s.control_publish_timeout_s == 5.0
    assert s.sse_heartbeat_s == 15
    assert s.sse_queue_max == 256
    assert (s.api_page_default, s.api_page_max) == (200, 1000)


@pytest.mark.parametrize("profile", ["laptop", "mac", "workstation"])
def test_every_profile_sets_the_control_plane_knobs(profile: str) -> None:
    lines = (PROFILES / f"{profile}.env").read_text(encoding="utf-8").splitlines()
    keys = {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}
    assert set(C1_KEYS) <= keys
