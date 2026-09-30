"""Fixtures for the router's tests. The shared event shapes live in `router_helpers`."""

from __future__ import annotations

from pathlib import Path

import pytest
from router.settings import RouterSettings
from router_helpers import FakeProducer


@pytest.fixture
def cfg(tmp_path: Path) -> RouterSettings:
    return RouterSettings(_env_file=None, data_dir=tmp_path)


@pytest.fixture
def producer() -> FakeProducer:
    return FakeProducer()


@pytest.fixture
def sink_path(tmp_path: Path) -> Path:
    return tmp_path / "sinks" / "wazuh" / "veyra.ndjson"
