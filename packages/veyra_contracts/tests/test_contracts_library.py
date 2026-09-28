"""library_match (C3 AC4): sshd and CEF samples find their pack; authsrv finds none."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from veyra_common.framing import split_lines
from veyra_contracts.library import library_match, load_library

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
REGISTRY = Path(os.environ.get("VEYRA_CONTRACTS_REPO", REPO.parent / "contracts-repo"))
needs_library = pytest.mark.skipif(
    not (REGISTRY / "library").is_dir(), reason="no contracts-repo checkout with library/"
)

PACK = """\
contract: demo_ok
version: 1
tenant: t_library
templates:
  - {id: ok, pattern: 'user=<u> OK', class: authentication, activity: logon, map: {user.name: $u}}
required: [time, user.name]
pii: [user.name]
"""


def lines(name: str) -> list[bytes]:
    return [f.raw for f in split_lines((CORPUS / name).read_bytes())]


def test_a_pack_matches_when_enough_samples_reach_tier_one(tmp_path: Path) -> None:
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "demo_ok.yaml").write_text(PACK, encoding="utf-8")
    (tmp_path / "library" / "broken.yaml").write_text("contract: [", encoding="utf-8")
    packs = load_library(tmp_path)
    assert [p.path for p in packs] == ["library/demo_ok.yaml"]  # the broken one is skipped

    samples = ["user=a OK", "user=b OK", "user=c OK", "user=d OK", "something else"]
    [match] = library_match(samples, packs, threshold=0.8)
    assert (match.contract_id, match.tier1_pct, match.matched) == ("demo_ok", 0.8, True)
    [strict] = library_match(samples, packs, threshold=0.9)
    assert not strict.matched
    assert library_match([], packs, threshold=0.8) == []


@needs_library
def test_sshd_auth_lines_match_the_linux_sshd_pack() -> None:
    [best, *_] = library_match(lines("linux_sshd.log"), load_library(REGISTRY), threshold=0.8)
    assert (best.contract_id, best.tier1_pct, best.matched) == ("linux_sshd", 1.0, True)


@needs_library
def test_cef_lines_match_the_acme_pack_first() -> None:
    ranked = library_match(lines("acme_ngfw_cef.log"), load_library(REGISTRY), threshold=0.8)
    assert [m.contract_id for m in ranked if m.matched] == ["acme_ngfw_cef", "generic_cef"]


@needs_library
def test_authsrv_lines_match_no_pack() -> None:
    samples = lines("authsrv_t1_ok.log") + lines("authsrv_t2_session.log")
    assert not any(m.matched for m in library_match(samples, load_library(REGISTRY), threshold=0.8))
