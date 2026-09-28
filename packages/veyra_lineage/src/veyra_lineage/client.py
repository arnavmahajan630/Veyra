"""The one place a ClickHouse client is built (IF-CH-SCHEMA, owner B).

``VEYRA_CH_URL`` wins over ``VEYRA_CLICKHOUSE_URL`` when set. Clients are created without
a session id, so one client can serve concurrent requests (evidence-api threads, the
benchmark) — sessions in ClickHouse are single-query-at-a-time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import unquote, urlparse

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from veyra_common.settings import Settings, settings


def ch_url(cfg: Settings | None = None) -> str:
    s = cfg or settings
    return s.ch_url or s.clickhouse_url


def make_client(
    cfg: Settings | None = None, *, database: str | None = None, **overrides: Any
) -> Client:
    """A new client for ``cfg``. ``database=""`` connects without selecting one."""
    s = cfg or settings
    url = urlparse(ch_url(s))
    kwargs: dict[str, Any] = {
        "interface": url.scheme or "http",
        "host": url.hostname or "localhost",
        "port": url.port or (8443 if url.scheme == "https" else 8123),
        "username": unquote(url.username) if url.username else s.clickhouse_user,
        "password": unquote(url.password) if url.password else s.clickhouse_password,
        "database": s.clickhouse_db if database is None else database,
        "autogenerate_session_id": False,
        "connect_timeout": 5,
        "send_receive_timeout": 60,
    }
    kwargs.update(overrides)
    return clickhouse_connect.get_client(**kwargs)


@lru_cache(maxsize=1)
def default_client() -> Client:
    """Process-wide client for the query library (thread-safe without a session)."""
    return make_client()
