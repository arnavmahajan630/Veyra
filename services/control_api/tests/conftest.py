"""Shared fixtures: settings rooted in tmp_path, a SQLite engine, a fake Kafka producer,
a controllable clock, a seeded contracts-repo, and the whole app in-process."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from control_api.app import create_app
from control_api.context import AppContext, build_context, first_boot
from control_api.contracts_repo import ensure_repo
from control_api.db import init_db, make_engine
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session as DbSession

from veyra_common.settings import Settings

START_NS = 1_790_000_000 * 1_000_000_000
# The contract registry is a separate repository checked out beside Veyra.
_REGISTRY = os.environ.get(
    "VEYRA_CONTRACTS_REPO", Path(__file__).resolve().parents[4] / "contracts-repo"
)
SEED_CONTRACTS = Path(_REGISTRY).resolve() / "t_ntro_core"


class FakeProducer:
    """Records produce() calls; `fail_with`/`unacked` simulate a broker that misbehaves."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None, bytes | None]] = []
        self.fail_with: str | None = None
        self.unacked = 0

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: str | bytes | None = None,
        on_delivery: Callable[[Any, Any], None] | None = None,
    ) -> None:
        text_key = key.decode() if isinstance(key, bytes) else key
        self.messages.append((topic, text_key, value))
        if on_delivery is not None:
            on_delivery(self.fail_with, None)

    def flush(self, timeout: float = 0) -> int:
        return self.unacked

    def latest(self, topic: str) -> dict[str, Any]:
        """Compacted view of one topic: last value per key (None = tombstone)."""
        out: dict[str, Any] = {}
        for t, key, value in self.messages:
            if t == topic and key is not None:
                out[key] = None if value is None else json.loads(value)
        return out


class FakeClock:
    def __init__(self, start_ns: int = START_NS) -> None:
        self.ns = start_ns

    def __call__(self) -> int:
        return self.ns

    def advance(self, seconds: float) -> None:
        self.ns += int(seconds * 1_000_000_000)


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # same accepted pattern as test_capi_settings.py
        data_dir=tmp_path / "data",
        control_db=tmp_path / "data" / "control" / "control.db",
        contracts_repo=tmp_path / "contracts-repo",
        inventory_file=tmp_path / "edge" / "inventory" / "sources.csv",
        inventory_reload_stamp=tmp_path / "edge" / "reload.stamp",
        demo_mode=True,
    )


@pytest.fixture
def engine(cfg: Settings) -> Iterator[Engine]:
    e = make_engine(cfg.control_db)
    init_db(e)
    yield e
    e.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[DbSession]:
    with DbSession(engine) as session:
        yield session


@pytest.fixture
def producer() -> FakeProducer:
    return FakeProducer()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def seeded_repo(cfg: Settings) -> Path:
    if not SEED_CONTRACTS.is_dir():
        pytest.skip(f"needs the contracts repository checked out at {SEED_CONTRACTS.parent}")
    target = cfg.contracts_repo / "t_ntro_core"
    target.mkdir(parents=True)
    for path in sorted(SEED_CONTRACTS.glob("*.yaml")):
        shutil.copy(path, target / path.name)
    ensure_repo(cfg.contracts_repo)
    return cfg.contracts_repo


@pytest.fixture
def ctx(
    cfg: Settings, seeded_repo: Path, producer: FakeProducer, clock: FakeClock
) -> Iterator[AppContext]:
    context = build_context(cfg, producer, clock=clock)
    first_boot(context)
    yield context
    context.engine.dispose()


@pytest.fixture
def client(ctx: AppContext) -> Iterator[TestClient]:
    with TestClient(create_app(ctx)) as test_client:
        yield test_client


@pytest.fixture
def login(client: TestClient) -> Callable[..., None]:
    def _login(email: str, password: str = "veyra-demo") -> None:
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text

    return _login
