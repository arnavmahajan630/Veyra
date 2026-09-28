"""The bench's scoring and case building (C4 bench)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from llm_bench import build_cases, score

REPO = Path(__file__).resolve().parents[2]
REGISTRY = Path(os.environ.get("VEYRA_CONTRACTS_REPO", REPO.parent / "contracts-repo"))


def test_score_is_pair_precision_and_recall() -> None:
    expected = {"user.name": "a", "src_endpoint.ip": "1.2.3.4", "status_id": {"const": 2}}
    got = {"user.name": "a", "src_endpoint.ip": "5.6.7.8", "dst_endpoint.ip": "1.2.3.4"}
    assert score(expected, got) == (1 / 3, 1 / 3)
    assert score({}, {}) == (1.0, 1.0)


@pytest.mark.skipif(not (REGISTRY / "library").is_dir(), reason="no contracts-repo library")
def test_cases_come_from_the_library_and_the_extras() -> None:
    cases = build_cases(REGISTRY, REPO / "bench" / "llm_golden" / "extra.json")
    assert len(cases) >= 20
    sshd = next(c for c in cases if c["id"] == "linux_sshd/failed_password")
    assert sshd["expected"]["user.name"] == "root"
