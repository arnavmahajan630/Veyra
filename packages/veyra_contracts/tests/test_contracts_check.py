"""`python -m veyra_contracts.check`: the whole registry, compiled, linted and golden-tested."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from veyra_contracts.check import find_contracts, main

CONTRACT = """\
contract: demo_auth
version: 1
tenant: t_demo
templates:
  - id: ok
    pattern: 'user=<user> OK login'
    class: authentication
    activity: logon
    map: {user.name: $user}
required: [time, user.name]
pii: [user.name]
tests:
  - {sample: samples/demo_auth/ok_1.log, expect: expected/demo_auth/ok_1.json}
"""

REGISTRY = Path(
    os.environ.get("VEYRA_CONTRACTS_REPO", Path(__file__).resolve().parents[4] / "contracts-repo")
)


def _registry(root: Path) -> Path:
    (root / "t_demo").mkdir(parents=True)
    (root / "t_demo" / "demo_auth.yaml").write_text(CONTRACT, encoding="utf-8")
    (root / "samples" / "demo_auth").mkdir(parents=True)
    (root / "samples" / "demo_auth" / "ok_1.log").write_bytes(b"user=r.patil OK login\n")
    (root / ".git").mkdir()
    (root / ".git" / "ignored.yaml").write_text("not: a contract", encoding="utf-8")
    return root


def test_hidden_directories_are_skipped(tmp_path: Path) -> None:
    root = _registry(tmp_path)
    assert [p.relative_to(root).as_posix() for p in find_contracts(root)] == [
        "t_demo/demo_auth.yaml"
    ]


def test_update_then_check_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _registry(tmp_path)
    assert main([str(root)]) == 1  # expected file not written yet
    assert main([str(root), "--update"]) == 0
    assert main([str(root)]) == 0
    assert "1/1 contracts ok" in capsys.readouterr().out


def test_a_compile_error_fails_with_its_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _registry(tmp_path)
    (root / "t_demo" / "broken.yaml").write_text(
        CONTRACT.replace("demo_auth", "broken").replace("activity: logon", "activity: nap"),
        encoding="utf-8",
    )
    assert main([str(root), "--update"]) == 1
    out = capsys.readouterr().out
    assert "FAIL t_demo/broken.yaml" in out and "line" in out


@pytest.mark.skipif(not (REGISTRY / "t_ntro_core").is_dir(), reason="no contracts-repo checkout")
def test_the_real_registry_passes() -> None:
    """C2 AC1: the library contracts compile, are deterministic and pass their goldens."""
    assert main([str(REGISTRY)]) == 0
