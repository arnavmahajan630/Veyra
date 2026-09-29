"""The integrity service's own knobs, on top of the shared ones (IF-ENV).

Shared: ``VEYRA_MERKLE_WINDOW_SECONDS`` (60), ``VEYRA_DATA_DIR`` (which derives
``vault_dir``/``keys_dir``), ``VEYRA_KEY_PROVIDER``.
"""

from __future__ import annotations

from veyra_common.settings import ServiceSettings


class IntegritySettings(ServiceSettings):
    service_name: str = "integrity"
    metrics_port: int = 8204  # IF-PORTS

    # How often to look for newly sealed segments.
    integrity_scan_seconds: float = 5.0
    # Leave the current window alone until it is this many seconds old, so a segment
    # sealed at the very end of a window is not missed by a root signed too eagerly.
    integrity_window_lag_seconds: int = 5
