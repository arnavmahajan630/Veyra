"""C1 task 8 against the real broker: control messages arrive within 1 s of the request."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import cast

import pytest
from control_api.app import create_app
from control_api.context import build_context, first_boot
from control_api.contracts_repo import ensure_repo
from control_api.publisher import ProducerLike
from fastapi.testclient import TestClient

from veyra_common.kafka import make_consumer, make_producer
from veyra_common.settings import Settings
from veyra_common.topics import TOPIC_CONTROL

pytestmark = pytest.mark.int

BOOTSTRAP = os.environ.get("VEYRA_KAFKA_BOOTSTRAP", "localhost:29092")
# The contract registry is a separate repository checked out beside Veyra.
_REGISTRY = os.environ.get(
    "VEYRA_CONTRACTS_REPO", Path(__file__).resolve().parents[4] / "contracts-repo"
)
SEED_CONTRACTS = Path(_REGISTRY).resolve() / "t_ntro_core"


def wait_for(consumer, key: str, predicate, timeout_s: float) -> float:
    """Seconds until a message with `key` satisfies `predicate`, or fail."""
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        msg = consumer.poll(0.05)
        if msg is None or msg.error():
            continue
        if (
            msg.key()
            and msg.key().decode() == key
            and predicate(json.loads(msg.value() or b"null"))
        ):
            return time.monotonic() - started
    pytest.fail(f"{key} not seen on {TOPIC_CONTROL} within {timeout_s}s")


def test_source_and_key_reach_control_within_one_second(tmp_path: Path) -> None:
    if not SEED_CONTRACTS.is_dir():
        pytest.skip(f"needs the contracts repository checked out at {SEED_CONTRACTS.parent}")
    cfg = Settings(
        _env_file=None,  # type: ignore[call-arg]  # same accepted pattern as test_capi_settings.py
        kafka_bootstrap=BOOTSTRAP,
        data_dir=tmp_path / "data",
        control_db=tmp_path / "data" / "control.db",
        contracts_repo=tmp_path / "contracts-repo",
        inventory_file=tmp_path / "edge" / "sources.csv",
        inventory_reload_stamp=tmp_path / "edge" / "reload.stamp",
        demo_mode=True,
    )
    (cfg.contracts_repo / "t_ntro_core").mkdir(parents=True)
    for path in SEED_CONTRACTS.glob("*.yaml"):
        shutil.copy(path, cfg.contracts_repo / "t_ntro_core" / path.name)
    ensure_repo(cfg.contracts_repo)

    # confluent_kafka.Producer's produce() is a structural superset of ProducerLike
    # (extra keyword params with defaults); the cast documents that compatibility.
    ctx = build_context(cfg, cast(ProducerLike, make_producer(cfg=cfg)))
    first_boot(ctx)
    consumer = make_consumer(
        f"capi-int-{uuid.uuid4().hex[:8]}", [TOPIC_CONTROL], cfg=cfg, auto_offset_reset="latest"
    )
    deadline = time.monotonic() + 10
    while not consumer.assignment() and time.monotonic() < deadline:
        consumer.poll(0.1)
    assert consumer.assignment(), "consumer never got the control partition"

    source_id = f"src_int_{uuid.uuid4().hex[:8]}"
    try:
        with TestClient(create_app(ctx)) as client:
            client.post("/auth/login", json={"email": "admin@veyra", "password": cfg.demo_password})
            created = client.post(
                "/sources",
                json={
                    "id": source_id, "tenant_id": "t_maha_power", "name": "int", "vendor": "custom",
                    "zone": "dmz", "transport": "http_push",
                },
            )  # fmt: skip
            assert created.status_code == 201, created.text
            assert wait_for(consumer, f"source:{source_id}", lambda v: v is not None, 5) < 1.0

            key_id = client.post(f"/sources/{source_id}/keys", json={}).json()["key_id"]
            is_active = lambda v: v["status"] == "active"  # noqa: E731
            assert wait_for(consumer, f"apikey:{key_id}", is_active, 5) < 1.0

            client.post(f"/keys/{key_id}/revoke")
            is_revoked = lambda v: v["status"] == "revoked"  # noqa: E731
            assert wait_for(consumer, f"apikey:{key_id}", is_revoked, 5) < 1.0
    finally:
        consumer.close()
        ctx.engine.dispose()
