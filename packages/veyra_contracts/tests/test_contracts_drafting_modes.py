"""Modes (C4): live, cache, live_then_cache, heuristic; badges; review flags."""

from __future__ import annotations

from pathlib import Path

from veyra_common.framing import split_lines
from veyra_contracts.drafting.cache import DraftCache
from veyra_contracts.drafting.drafter import Drafter
from veyra_contracts.drafting.ollama import DraftFailed
from veyra_contracts.drafting.request import Prepared, build
from veyra_contracts.drafting.schema import DraftResponse

CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"
T3_SIG = "t_3c85a1bfbf81"
GOOD = {
    "class": "authentication",
    "activity": "logon",
    "confidence": "high",
    "mappings": [
        {"ocsf_path": "user.name", "token": "k2"},
        {"ocsf_path": "src_endpoint.ip", "token": "k6"},
        {"ocsf_path": "dst_endpoint.ip", "token": "k8"},
        {"ocsf_path": "status_id", "const": 2},
    ],
}


def t3() -> Prepared:
    raws = [f.raw for f in split_lines((CORPUS / "authsrv_t3_failed.log").read_bytes())][:8]
    return build(raws, template_sig=T3_SIG)


class FakeModel:
    model = "qwen2.5:3b"

    def __init__(self, answer: dict | None) -> None:
        self.answer = answer
        self.calls = 0

    def draft(self, prepared: Prepared) -> tuple[DraftResponse, str]:
        self.calls += 1
        if self.answer is None:
            raise DraftFailed("the model timed out")
        return DraftResponse.model_validate(self.answer), "raw"


def test_live_uses_the_model_and_badges_it(tmp_path: Path) -> None:
    outcome = Drafter(mode="live", cache=DraftCache(tmp_path), client=FakeModel(GOOD)).draft(t3())
    assert outcome.source == "llm:qwen2.5:3b" and outcome.review == []


def test_live_falls_back_to_the_heuristic(tmp_path: Path) -> None:
    outcome = Drafter(mode="live", cache=DraftCache(tmp_path), client=FakeModel(None)).draft(t3())
    assert outcome.source == "heuristic"
    assert "timed out" in outcome.notes[0]


def test_live_then_cache_writes_the_cache_then_serves_it(tmp_path: Path) -> None:
    cache = DraftCache(tmp_path)
    Drafter(mode="live_then_cache", cache=cache, client=FakeModel(GOOD)).draft(t3())
    assert cache.get(T3_SIG) is not None
    down = Drafter(mode="live_then_cache", cache=cache, client=FakeModel(None)).draft(t3())
    assert down.source == "cache:qwen2.5:3b"


def test_cache_mode_never_calls_the_model(tmp_path: Path) -> None:
    model = FakeModel(GOOD)
    miss = Drafter(mode="cache", cache=DraftCache(tmp_path), client=model).draft(t3())
    assert miss.source == "heuristic" and model.calls == 0


def test_every_mode_satisfies_ac1(tmp_path: Path) -> None:
    """C4 AC1 in all modes: the same four mappings whatever the source."""
    cache = DraftCache(tmp_path)
    Drafter(mode="live_then_cache", cache=cache, client=FakeModel(GOOD)).draft(t3())
    for mode in ("live", "cache", "heuristic"):
        outcome = Drafter(mode=mode, cache=cache, client=FakeModel(GOOD)).draft(t3())
        paths = {m.ocsf_path for m in outcome.response.mappings}
        assert paths >= {"user.name", "src_endpoint.ip", "dst_endpoint.ip", "status_id"}, mode


def test_disagreements_with_the_heuristic_are_flagged_for_review(tmp_path: Path) -> None:
    swapped = GOOD | {
        "mappings": [
            {"ocsf_path": "src_endpoint.ip", "token": "k8"},
            {"ocsf_path": "dst_endpoint.ip", "token": "k6"},
        ]
    }
    outcome = Drafter(mode="live", cache=DraftCache(tmp_path), client=FakeModel(swapped)).draft(
        t3()
    )
    assert sorted(outcome.review) == ["dst_endpoint.ip", "src_endpoint.ip"]


def test_a_per_request_mode_overrides_the_default(tmp_path: Path) -> None:
    drafter = Drafter(mode="live", cache=DraftCache(tmp_path), client=FakeModel(GOOD))
    assert drafter.draft(t3(), mode="heuristic").source == "heuristic"
