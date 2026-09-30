"""The router's own settings section.

Every knob A6 needs already exists in `veyra_common.settings.Settings` and in all three profiles
(`sink_rotate_bytes`, `route_queue_max`, `route_breaker_fails`, `route_fsync_ms`,
`wazuh_remote_host/port`), so nothing new is invented here — P5: profile-driven, no magic numbers.
"""

from __future__ import annotations

from pathlib import Path

from veyra_common.settings import ServiceSettings


class RouterSettings(ServiceSettings):
    """Service section for the router."""

    service_name: str = "router"
    metrics_port: int = 8202  # IF-PORTS
    consumer_group: str = "router"
    instance: str = "0"
    # Used only when the `routes` control key is absent, so the router is useful before C1 boots.
    routes_file: Path = Path("services/router/routes.default.yaml")
