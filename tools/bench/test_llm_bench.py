"""The bench's scoring and case building (C4 bench)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from llm_bench import NOTES_MARK, build_cases, differences, score, write_report

from veyra_contracts.drafting.options import paths_for
from veyra_contracts.drafting.request import build

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


def test_differences_name_what_is_wrong_missing_and_extra() -> None:
    expected = {"user.name": "a", "src_endpoint.ip": "1.2.3.4", "status_id": {"const": 2}}
    got = {"user.name": "a", "src_endpoint.ip": "5.6.7.8", "dst_endpoint.port": "22"}
    assert differences(expected, got) == [
        "src_endpoint.ip: '5.6.7.8', want '1.2.3.4'",
        "status_id: missing, want {'const': 2}",
        "dst_endpoint.port: '22' not wanted",
    ]
    assert differences(expected, expected) == []


def test_the_report_rewrite_keeps_the_hand_written_notes(tmp_path: Path) -> None:
    report = tmp_path / "C4-bench-test.md"
    write_report(report, ["# first", "| row |"])
    assert report.read_text(encoding="utf-8") == "# first\n| row |\n"
    report.write_text(f"# first\n| row |\n\n{NOTES_MARK} Keep. -->\n\nMeasured by hand.\n", "utf-8")
    write_report(report, ["# second", "| new row |"])
    text = report.read_text(encoding="utf-8")
    assert text.startswith("# second\n| new row |\n\n" + NOTES_MARK)
    assert text.endswith("Measured by hand.\n") and "| row |" not in text


@pytest.mark.skipif(not (REGISTRY / "library").is_dir(), reason="no contracts-repo library")
def test_the_answer_lists_never_exclude_a_golden_mapping() -> None:
    """Every expected token mapping of every case is among the fields that token is offered."""
    for case in build_cases(REGISTRY, REPO / "bench" / "llm_golden" / "extra.json"):
        raws = [s.encode("utf-8") for s in case["samples"]]
        prepared = build(raws, template_sig=case["id"], layers=case["layers"])
        variable = [t for t in prepared.tokens if t.id in prepared.variable]
        for path, value in case["expected"].items():
            holders = [t for t in variable if t.value == value]
            if holders:
                assert any(path in paths_for(t) for t in holders), (case["id"], path, value)
