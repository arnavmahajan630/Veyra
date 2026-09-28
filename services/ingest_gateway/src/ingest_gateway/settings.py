"""The gateway's own settings section.

The three A2 knobs (`gateway_default_quota_eps`, `gateway_ack_timeout_ms`,
`gateway_max_body_bytes`) already live in `veyra_common.settings.Settings` and in `profiles/*.env`,
so nothing new is invented here (P5: profile-driven, no magic numbers in code).
"""

from __future__ import annotations

from veyra_common.settings import ServiceSettings


class GatewaySettings(ServiceSettings):
    """Service section for the ingest gateway."""

    service_name: str = "ingest_gateway"
    metrics_port: int = 8088  # IF-PORTS: the HTTP port *is* the metrics port here
    consumer_group: str = "ingest-gateway"
    instance: str = "0"
    # What to stamp when a key names a source the control topic has no record for yet. The demo
    # issues a key during onboarding before the source row is published, and the alternative —
    # refusing the event — would break P2.
    default_vendor: str = "custom"
    default_zone: str = "dmz"
