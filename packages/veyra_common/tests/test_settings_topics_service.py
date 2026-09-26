"""Settings (P5: no magic numbers), IF-TOPICS specs, and the ServiceApp contract."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from veyra_common.service import ServiceApp
from veyra_common.settings import Settings
from veyra_common.topics import (
    CATEGORIES,
    SEED_VENDORS,
    TOPIC_CONTROL,
    category_for_class,
    norm_topic,
    raw_topic,
    topic_specs,
)

PROFILES = Path(__file__).resolve().parents[3] / "profiles"


def test_laptop_defaults_match_the_profile_table() -> None:
    """docs/plan/03_INFRA_PROFILES.md §2 is the source of truth for these."""
    s = Settings(_env_file=None)
    assert s.profile == "laptop"
    assert s.raw_partitions_per_vendor == 3
    assert s.norm_partitions == 3
    assert s.segment_max_bytes == 2 * 1024 * 1024
    assert s.segment_max_seconds == 20
    assert s.merkle_window_seconds == 60
    assert s.max_event_bytes == 65536
    assert s.engine_budget_us == 5000
    assert s.peel_max_depth == 4
    assert s.gateway_default_quota_eps == 500
    assert s.demo_eps_baseline == 15
    assert s.llm_mode == "live_then_cache"
    assert s.key_provider == "local"


def test_env_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEYRA_MAX_EVENT_BYTES", "131072")
    monkeypatch.setenv("VEYRA_PROFILE", "workstation")
    s = Settings(_env_file=None)
    assert s.max_event_bytes == 131072
    assert s.profile == "workstation"


def test_derived_paths_follow_data_dir() -> None:
    s = Settings(_env_file=None, data_dir=Path("/srv/veyra"))
    assert s.vault_dir == Path("/srv/veyra/vault")
    assert s.keys_dir == Path("/srv/veyra/keys")
    assert s.sinks_dir == Path("/srv/veyra/sinks")


@pytest.mark.parametrize("profile", ["laptop", "mac", "workstation"])
def test_every_profile_file_only_sets_known_knobs(profile: str) -> None:
    """A typo in a profile file must fail here, not silently do nothing."""
    known = {f"VEYRA_{name.upper()}" for name in Settings.model_fields}
    path = PROFILES / f"{profile}.env"
    unknown = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("=", 1)[0]
        if key not in known:
            unknown.append(key)
    assert not unknown, f"{path.name} sets unknown knobs: {unknown}"


def test_profile_files_are_loadable(monkeypatch: pytest.MonkeyPatch) -> None:
    for line in (PROFILES / "workstation.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            monkeypatch.setenv(key, value)
    s = Settings(_env_file=None)
    assert s.profile == "workstation"
    assert s.normalizer_replicas == 6
    assert s.raw_partitions_per_vendor == 12


def test_topic_specs_cover_if_topics() -> None:
    specs = {spec.name: spec for spec in topic_specs(Settings(_env_file=None))}
    for vendor in SEED_VENDORS:
        assert raw_topic(vendor) in specs
    for category in CATEGORIES:
        assert norm_topic(category) in specs
    for name in ("replay.raw", "lineage", "dlq", "shadow", "vault_index", "receipts", "audit"):
        assert name in specs
    assert specs["raw.linux"].partitions == 3
    assert specs["dlq"].retention_days == 14
    control = specs[TOPIC_CONTROL]
    assert control.compacted and control.partitions == 1
    cfg = control.config(replication=1)
    assert cfg["cleanup.policy"] == "compact" and cfg["retention.ms"] == "-1"


def test_partitions_scale_with_the_profile() -> None:
    specs = {s.name: s for s in topic_specs(Settings(_env_file=None, raw_partitions_per_vendor=12))}
    assert specs["raw.linux"].partitions == 12


def test_class_to_category_map() -> None:
    assert category_for_class(3002) == "iam"
    assert category_for_class(4001) == "network"
    assert category_for_class(1007) == "system"
    assert category_for_class(0) == "uncategorized"
    assert category_for_class(99999) == "uncategorized"


def test_service_app_health_and_metrics() -> None:
    app = ServiceApp(name="test-svc", metrics_port=18299, cfg=Settings(_env_file=None))
    try:
        with pytest.raises(urllib.error.HTTPError) as unready:
            urllib.request.urlopen("http://127.0.0.1:18299/healthz")
        assert unready.value.code == 503, "a service must not report healthy before it is ready"
        app.mark_ready()
        with urllib.request.urlopen("http://127.0.0.1:18299/healthz") as resp:
            body = json.loads(resp.read())
            assert resp.status == 200
            assert body == {
                "service": "test-svc",
                "ready": True,
                "stopping": False,
                "profile": "laptop",
            }
        with urllib.request.urlopen("http://127.0.0.1:18299/metrics") as resp:
            text = resp.read().decode()
            assert 'veyra_service_ready{service="test-svc"} 1.0' in text
    finally:
        app.stop()


def test_stop_hooks_run_in_reverse_order() -> None:
    calls: list[str] = []
    app = ServiceApp(
        name="hooks", metrics_port=18298, cfg=Settings(_env_file=None), serve_http=False
    )
    app.on_stop(lambda: calls.append("first"))
    app.on_stop(lambda: calls.append("second"))
    app.stop()
    assert calls == ["second", "first"]
    app.stop()  # idempotent
    assert calls == ["second", "first"]
