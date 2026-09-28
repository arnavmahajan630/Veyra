"""C3 knobs exist with laptop defaults, and every profile sets them (P5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from veyra_common.settings import Settings

PROFILES = Path(__file__).resolve().parents[3] / "profiles"
C3_KEYS = [
    "VEYRA_DRIFT_DEBOUNCE_MS",
    "VEYRA_DRIFT_MAX_SAMPLES",
    "VEYRA_DRIFT_DRAIN_SIM_TH",
    "VEYRA_DRIFT_DRAIN_DEPTH",
    "VEYRA_DRIFT_CHECKPOINT_MS",
    "VEYRA_DRIFT_POLL_MS",
    "VEYRA_DRIFT_WORKER_PORT",
    "VEYRA_DRIFT_WORKER_URL",
    "VEYRA_CONTROL_API_URL",
    "VEYRA_LIBRARY_MATCH_MIN",
]


def test_drift_knobs_have_laptop_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.drift_debounce_ms, s.drift_max_samples, s.drift_checkpoint_ms) == (2000, 5, 5000)
    assert (s.drift_drain_sim_th, s.drift_drain_depth, s.drift_poll_ms) == (0.4, 4, 500)
    assert (s.drift_worker_port, s.drift_worker_url) == (8206, "http://drift-worker:8206")
    assert (s.control_api_url, s.library_match_min) == ("http://control-api:8000", 0.8)


@pytest.mark.parametrize("profile", ["laptop", "mac", "workstation"])
def test_every_profile_sets_the_drift_knobs(profile: str) -> None:
    lines = (PROFILES / f"{profile}.env").read_text(encoding="utf-8").splitlines()
    keys = {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}
    assert set(C3_KEYS) <= keys
