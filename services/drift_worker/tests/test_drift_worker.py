"""The drift worker core (C3): grouping, Drain3 templates, emission, debounce, restart."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from drift_worker.worker import DriftWorker

from veyra_common.models import DlqRecord
from veyra_common.settings import Settings

CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"
T3_SIG = "t_3c85a1bfbf81"
T3_TEMPLATE = "user=<*> FAILED login from <*> via <*> attempts:<*>"
NS = 1_000_000_000


def t3_texts() -> list[str]:
    """The template text of each T3 line: the JSON ``msg`` inside the syslog body."""
    raw = (CORPUS / "authsrv_t3_failed.log").read_text(encoding="utf-8")
    return re.findall(r'"msg":"([^"]*)"', raw)


def record(i: int, text: str, *, sig: str = T3_SIG, source: str = "src_authsrv_01") -> DlqRecord:
    return DlqRecord(
        event_uid=f"0192a4f0-0000-7000-8000-{i:012d}",
        tenant_id="t_maha_power",
        source_id=source,
        tier=4,
        reason_code="no_template_match",
        template_sig=sig,
        text_masked=text,
        produced_at=f"2026-09-26T14:05:{i:02d}.000000000Z",
    )


class Clock:
    def __init__(self) -> None:
        self.ns = 1_790_000_000 * NS

    def __call__(self) -> int:
        return self.ns


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", drift_min_cluster=5)  # type: ignore[call-arg]


@pytest.fixture
def posted() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def worker(cfg: Settings, posted: list[dict[str, Any]], clock: Clock) -> DriftWorker:
    return DriftWorker(cfg, posted.append, clock=clock)


def test_t3_lines_become_one_group_with_distinct_samples(worker: DriftWorker) -> None:
    texts = t3_texts()
    for i, text in enumerate(texts):
        worker.handle(record(i, text))
    [group] = worker.store.groups.values()
    assert group.count == len(texts) == 14
    assert len(group.samples_masked) == 5 == len(set(group.samples_masked))
    assert len(group.sample_event_uids) == 5
    assert (group.first_seen, group.last_seen) == (
        "2026-09-26T14:05:00.000000000Z",
        "2026-09-26T14:05:13.000000000Z",
    )


def test_the_drain_template_reads_like_the_phase_doc(worker: DriftWorker) -> None:
    for i, text in enumerate(t3_texts()[:8]):
        worker.handle(record(i, text))
    [payload] = worker.snapshot()
    assert payload["drain_template"] == T3_TEMPLATE


def test_emits_at_min_cluster_then_once_per_debounce(
    worker: DriftWorker, posted: list[dict[str, Any]], clock: Clock
) -> None:
    texts = t3_texts()
    for i in range(4):
        worker.handle(record(i, texts[i]))
    assert worker.tick() == 0
    worker.handle(record(4, texts[4]))
    assert worker.tick() == 1 and posted[-1]["count"] == 5
    worker.handle(record(5, texts[5]))
    assert worker.tick() == 0  # inside the 2 s debounce
    clock.ns += 2 * NS
    assert worker.tick() == 1 and posted[-1]["count"] == 6
    assert worker.tick() == 0  # nothing new


def test_flush_forces_small_groups_out(worker: DriftWorker, posted: list[dict[str, Any]]) -> None:
    worker.handle(record(0, t3_texts()[0]))
    assert worker.tick() == 0
    assert worker.tick(force=True) == 1
    assert set(posted[0]) == {
        "source_id", "template_sig", "related_sigs", "drain_template", "count",
        "samples_masked", "sample_event_uids", "first_seen", "last_seen",
    }  # fmt: skip


def test_a_failed_post_is_retried_next_tick(cfg: Settings, clock: Clock) -> None:
    calls: list[int] = []

    def flaky(payload: dict[str, Any]) -> None:
        calls.append(payload["count"])
        if len(calls) == 1:
            raise RuntimeError("control-api down")

    worker = DriftWorker(cfg, flaky, clock=clock)
    for i, text in enumerate(t3_texts()[:5]):
        worker.handle(record(i, text))
    assert worker.tick() == 0
    assert worker.tick() == 1 and calls == [5, 5]


def test_sigs_in_one_drain_cluster_are_related(worker: DriftWorker) -> None:
    texts = t3_texts()
    for i in range(3):
        worker.handle(record(i, texts[i], sig="t_aaaaaaaaaaaa"))
        worker.handle(record(10 + i, texts[i + 8], sig="t_bbbbbbbbbbbb"))
    payloads = {p["template_sig"]: p for p in worker.snapshot()}
    assert payloads["t_aaaaaaaaaaaa"]["related_sigs"] == ["t_bbbbbbbbbbbb"]
    assert payloads["t_bbbbbbbbbbbb"]["related_sigs"] == ["t_aaaaaaaaaaaa"]


def test_a_restart_keeps_groups_and_clusters(
    cfg: Settings, posted: list[dict[str, Any]], clock: Clock
) -> None:
    """C3 AC3: the chosen strategy persists counts next to the Drain3 state."""
    texts = t3_texts()
    first = DriftWorker(cfg, posted.append, clock=clock)
    for i in range(6):
        first.handle(record(i, texts[i]))
    first.tick()
    first.checkpoint()

    second = DriftWorker(cfg, posted.append, clock=clock)
    second.handle(record(6, texts[6]))
    [payload] = second.snapshot()
    assert payload["count"] == 7 and payload["drain_template"] == T3_TEMPLATE
    assert second.tick() == 0  # already emitted at 6, and still inside the debounce
    assert (cfg.state_dir / "drain3" / "src_authsrv_01.json").is_file()


def test_reset_forgets_groups_clusters_and_state(worker: DriftWorker, cfg: Settings) -> None:
    worker.handle(record(0, t3_texts()[0]))
    worker.checkpoint()
    worker.reset()
    assert worker.snapshot() == []
    assert not (cfg.state_dir / "drift" / "groups.json").exists()
    assert list((cfg.state_dir / "drain3").glob("*.json")) == []


def test_unregistered_traffic_is_not_drift(worker: DriftWorker) -> None:
    assert worker.handle(record(0, "anything", source="unregistered")) is None
    assert worker.snapshot() == []


def test_an_event_with_no_text_is_grouped_without_a_template(worker: DriftWorker) -> None:
    """Tier 4 garbage (decode errors) has no masked text: grouped by sig, never clustered."""
    for i in range(2):
        worker.handle(record(i, "", sig="t_000000000000"))
    [payload] = worker.snapshot()
    assert (payload["count"], payload["drain_template"], payload["samples_masked"]) == (2, "", [])
