"""Shared fixtures: settings rooted in tmp_path, a SQLite engine, a fake Kafka producer,
a controllable clock, a seeded contracts-repo, fakes for evidence-api / raw Kafka / the
lineage watcher (C2), and the whole app in-process."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from control_api.app import create_app
from control_api.context import AppContext, build_context, first_boot
from control_api.contracts_repo import ensure_repo
from control_api.db import init_db, make_engine
from control_api.evidence import EventRef, EvidenceUnavailable
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session as DbSession

from veyra_common.models import Envelope
from veyra_common.settings import Settings
from veyra_contracts.drafting.cache import DraftCache
from veyra_contracts.drafting.drafter import Drafter

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


class FakeIndex:
    """evidence-api's template-events lookup, from a dict; ``down`` simulates an outage."""

    def __init__(self) -> None:
        self.events: dict[str, list[EventRef]] = {}
        self.down = False

    def template_events(self, sig: str, *, limit: int) -> list[EventRef]:
        if self.down:
            raise EvidenceUnavailable("evidence-api: connection refused")
        return self.events.get(sig, [])[:limit]


class FakeRawStore:
    def __init__(self) -> None:
        self.by_uid: dict[str, Envelope] = {}

    def envelopes(self, refs: Sequence[EventRef]) -> list[Envelope]:
        return [self.by_uid[r.event_uid] for r in refs if r.event_uid in self.by_uid]


class FakeWatcher:
    """Reports ``normalized`` events at once (``None`` = everything expected)."""

    def __init__(self) -> None:
        self.normalized: int | None = None
        self.calls: list[str] = []

    def watch(
        self,
        job_id: str,
        *,
        since_ms: int,
        expected: int,
        timeout_s: float,
        on_progress: Callable[[int], None],
    ) -> int:
        self.calls.append(job_id)
        count = expected if self.normalized is None else self.normalized
        on_progress(count)
        return count


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
def index() -> FakeIndex:
    return FakeIndex()


@pytest.fixture
def raw_store() -> FakeRawStore:
    return FakeRawStore()


@pytest.fixture
def watcher() -> FakeWatcher:
    return FakeWatcher()


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
    cfg: Settings,
    seeded_repo: Path,
    producer: FakeProducer,
    clock: FakeClock,
    index: FakeIndex,
    raw_store: FakeRawStore,
    watcher: FakeWatcher,
) -> Iterator[AppContext]:
    context = build_context(
        cfg, producer, clock=clock, index=index, raw=raw_store, watcher=watcher,
        spawn=lambda work: work(),  # replay jobs run inline in tests
        drafter=Drafter(mode="heuristic", cache=DraftCache(cfg.llm_cache_dir)),
    )  # fmt: skip
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


@pytest.fixture
def as_user(client: TestClient, login: Callable[..., None]) -> Callable[[str], None]:
    """Sign out, then sign in as ``email`` (the four-eyes tests switch users often)."""

    def _as(email: str) -> None:
        client.post("/auth/logout")
        login(email)

    return _as


@pytest.fixture
def authsrv_source(client: TestClient, as_user: Callable[[str], None]) -> None:
    """src_authsrv_01 exists (Beat 2 creates it) and author@maha is signed in."""
    from capi_helpers import SOURCE

    as_user("author@maha")
    assert client.post("/sources", json=SOURCE).status_code == 201
