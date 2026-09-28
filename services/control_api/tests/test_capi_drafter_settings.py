"""C4 knobs exist with laptop defaults, and every profile sets them (P5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from veyra_common.settings import Settings

PROFILES = Path(__file__).resolve().parents[3] / "profiles"
C4_KEYS = [
    "VEYRA_LLM_MAX_CONCURRENCY",
    "VEYRA_LLM_KEEP_ALIVE",
    "VEYRA_DRIFT_AUTODRAFT",
    "VEYRA_DRAFTER_TIMEZONE",
    "VEYRA_ONBOARDING_MAX_SAMPLES",
]


def test_drafter_knobs_have_laptop_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.llm_max_concurrency, s.llm_keep_alive, s.drift_autodraft) == (1, "30m", False)
    assert (s.drafter_timezone, s.onboarding_max_samples) == ("Asia/Kolkata", 20)


@pytest.mark.parametrize("profile", ["laptop", "mac", "workstation"])
def test_every_profile_sets_the_drafter_knobs(profile: str) -> None:
    lines = (PROFILES / f"{profile}.env").read_text(encoding="utf-8").splitlines()
    keys = {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}
    assert set(C4_KEYS) <= keys
