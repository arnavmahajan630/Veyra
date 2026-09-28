"""Every tunable knob in VEYRA, in one place (IF-ENV, principle P5).

No service may hard-code a limit, interval, size, partition count, model name or path.
Defaults here are the **laptop** profile values from docs/plan/03_INFRA_PROFILES.md §2;
``profiles/<name>.env`` overrides them, then ``.env.local``, then the real environment.

Usage::

    from veyra_common.settings import settings          # process-wide singleton
    from veyra_common.settings import Settings          # for tests / explicit construction

    Settings(max_event_bytes=1024)                      # tests override by keyword
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

Profile = Literal["laptop", "mac", "workstation"]
KeyProviderName = Literal["local", "openbao"]
LlmMode = Literal["live", "cache", "live_then_cache", "heuristic"]
WazuhMode = Literal["local", "remote"]


class Settings(BaseSettings):
    """All VEYRA_* settings. Laptop defaults; see docs/plan/03_INFRA_PROFILES.md."""

    model_config = SettingsConfigDict(
        env_prefix="VEYRA_",
        env_file=(".env.local",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ identity
    profile: Profile = "laptop"
    data_dir: Path = Path("data")
    demo_mode: bool = False
    log_level: str = "INFO"

    # ------------------------------------------------------------------ §2.1 memory / CPU
    # Memory limits are consumed by compose as mem_limit strings, not by Python.
    mem_kafka: str = "1g"
    kafka_heap: str = "512m"
    mem_clickhouse: str = "1g"
    ch_max_memory: str = "800m"
    mem_wazuh_indexer: str = "1.8g"
    wazuh_indexer_heap: str = "1g"
    mem_wazuh_manager: str = "700m"
    mem_wazuh_dashboard: str = "800m"
    mem_immudb: str = "256m"
    mem_vector: str = "128m"
    mem_py_service: str = "192m"

    normalizer_replicas: int = 1
    router_replicas: int = 1
    raw_partitions_per_vendor: int = 3
    norm_partitions: int = 3
    kafka_replication: int = 1
    # librdkafka refuses a transactional producer whose delivery timeout exceeds the
    # transaction timeout, so both are knobs and the default keeps them equal.
    kafka_delivery_timeout_ms: int = 120_000
    kafka_txn_timeout_ms: int = 120_000

    # ------------------------------------------------------------------ kafka / infra endpoints
    kafka_bootstrap: str = "kafka:9092"
    # Host-side bootstrap for tools run outside the compose network (make topics,
    # make test-int, demo/tools/*): kafka publishes an EXTERNAL listener on 29092.
    kafka_bootstrap_host: str = "localhost:29092"
    clickhouse_url: str = "http://clickhouse:8123"
    # Host-side override for tools and tests outside the compose network. `veyra_lineage.client`
    # (B) reads this first and falls back to `clickhouse_url`; empty means "not overridden".
    ch_url: str = ""
    clickhouse_db: str = "veyra"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    immudb_host: str = "immudb"
    immudb_pg_port: int = 5432
    # Host-published port for immudb's pg wire. Inside veyra_net it is always 5432; the
    # host mapping is a knob because a local PostgreSQL usually already owns 5432.
    immudb_pg_host_port: int = 5433
    immudb_grpc_port: int = 3322
    immudb_user: str = "immudb"
    immudb_password: str = "immudb"

    # Retention per IF-TOPICS (days). `control` is compacted and never expires.
    retention_raw_days: int = 3
    retention_replay_days: int = 1
    retention_norm_days: int = 3
    retention_lineage_days: int = 3
    retention_dlq_days: int = 14
    retention_shadow_days: int = 1
    retention_vault_index_days: int = 3
    retention_receipts_days: int = 3
    retention_audit_days: int = 30
    dlq_partitions: int = 1
    shadow_partitions: int = 1
    receipts_partitions: int = 1
    audit_partitions: int = 1

    # --------------------------------------------------- §2.2 vault / integrity / lineage
    segment_max_bytes: int = 2 * 1024 * 1024  # 2 MB
    segment_max_seconds: int = 20
    merkle_window_seconds: int = 60
    zstd_level: int = 3
    lineage_ttl_days: int = 90
    key_provider: KeyProviderName = "local"
    vault_chattr: bool = True
    openbao_url: str = "http://openbao:8200"
    openbao_token: str = ""

    # --------------------------------------------------- §2.3 engine / ingestion limits
    max_event_bytes: int = 65536
    engine_budget_us: int = 5000
    peel_max_depth: int = 4
    gateway_default_quota_eps: int = 500

    # Edge (A1)
    edge_multiline_flush_ms: int = 500
    # 256 MiB + 32 B, which is Vector's minimum disk-buffer size (it rejects less).
    edge_buffer_bytes: int = 268_435_488

    # Gateway (A2)
    gateway_ack_timeout_ms: int = 5000
    gateway_max_body_bytes: int = 10 * 1024 * 1024

    # Normalizer (A3/A4)
    norm_batch_max: int = 500
    norm_batch_ms: int = 100
    poison_max_retries: int = 3

    # Router (A6)
    sink_rotate_bytes: int = 64 * 1024 * 1024
    route_queue_max: int = 10000
    route_breaker_fails: int = 5
    route_fsync_ms: int = 200
    wazuh_mode: WazuhMode = "local"
    # Image defaults; S2 rotates them with wazuh-passwords-tool. The dashboard and API
    # values are consumed by compose, but they live here so a typo in a profile fails a
    # test instead of silently doing nothing.
    wazuh_indexer_user: str = "admin"
    wazuh_indexer_password: str = "admin"
    wazuh_dashboard_password: str = "kibanaserver"
    wazuh_api_password: str = "MyS3cr37P450r.*-"
    wazuh_remote_host: str = ""
    wazuh_remote_port: int = 514

    # ------------------------------------------------------------------ §2.4 demo / LLM / drift
    demo_eps_baseline: int = 15
    bench_target_eps: int = 2000
    llm_model: str = "qwen2.5:3b"
    llm_num_ctx: int = 4096
    llm_timeout_s: int = 25
    llm_mode: LlmMode = "live_then_cache"
    ollama_url: str = "http://host.docker.internal:11434"
    drift_min_cluster: int = 5
    sse_tick_ms: int = 1000

    # ------------------------------------------------------------------ control plane (C1)
    control_api_port: int = 8000
    control_db: Path = Path("data/control/control.db")
    contracts_repo: Path = Path("../contracts-repo")  # separate repo, beside Veyra/
    inventory_file: Path = Path("edge/vector/inventory/sources.csv")
    inventory_reload_stamp: Path = Path("edge/vector/reload.stamp")
    session_ttl_min: int = 480
    demo_password: str = "veyra-demo"
    public_host: str = "localhost"
    control_publish_timeout_s: float = 5.0
    sse_heartbeat_s: int = 15
    sse_queue_max: int = 256
    api_page_default: int = 200
    api_page_max: int = 1000

    # ------------------------------------------------------------------ registry (C2)
    backtest_max: int = 200
    replay_max: int = 10000
    replay_timeout_s: int = 60
    replay_poll_ms: int = 500
    # evidence-api as Caddy forwards it: /api/lineage/* arrives at the service root.
    evidence_api_url: str = "http://evidence-api:8100"
    evidence_timeout_s: float = 5.0

    # ------------------------------------------------------------------ drift (C3)
    drift_debounce_ms: int = 2000
    drift_max_samples: int = 5
    drift_drain_sim_th: float = 0.4
    drift_drain_depth: int = 4
    drift_checkpoint_ms: int = 5000
    drift_poll_ms: int = 500
    drift_worker_port: int = 8206
    drift_worker_url: str = "http://drift-worker:8206"
    control_api_url: str = "http://control-api:8000"
    library_match_min: float = 0.8

    # ------------------------------------------------------------------ derived paths
    @computed_field  # type: ignore[prop-decorator]
    @property
    def vault_dir(self) -> Path:
        return self.data_dir / "vault"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sinks_dir(self) -> Path:
        return self.data_dir / "sinks"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_cache_dir(self) -> Path:
        return self.data_dir / "llm_cache"


class ServiceSettings(Settings):
    """Base for a service's own section.

    Services subclass this and add their own fields, so every service still sees the
    shared knobs::

        class RouterSettings(ServiceSettings):
            routes_file: Path = Path("services/router/routes.default.yaml")
    """

    service_name: str = Field(default="veyra-service", description="set by the service")
    metrics_port: int = 8200


def contracts_repo_path(cfg: Settings | None = None) -> Path:
    """Where the Log Contract registry is checked out.

    The registry is its own repository beside this one (C's 2026-09-28 DECISION), so this is a
    setting rather than a path inside the repo: ``VEYRA_CONTRACTS_REPO`` wins, else
    ``Settings.contracts_repo`` (``../contracts-repo``). Resolved, so callers can compare paths and
    print something a human can act on.
    """
    configured = os.environ.get("VEYRA_CONTRACTS_REPO") or (cfg or get_settings()).contracts_repo
    return Path(configured).expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (cached; tests construct Settings() directly)."""
    return Settings()


settings = get_settings()
