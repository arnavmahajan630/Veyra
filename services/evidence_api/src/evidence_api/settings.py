"""The evidence API's own knobs, on top of the shared ones (IF-ENV).

Shared: ``VEYRA_DATA_DIR`` (derives ``vault_dir``/``keys_dir``), ``VEYRA_KEY_PROVIDER``,
``VEYRA_MERKLE_WINDOW_SECONDS``.
"""

from __future__ import annotations

from veyra_common.settings import ServiceSettings


class EvidenceApiSettings(ServiceSettings):
    service_name: str = "evidence_api"
    metrics_port: int = 8100  # IF-PORTS

    api_host: str = "0.0.0.0"
    api_port: int = 8100
    # Safety rail for the prototype's linear vault scan.
    evidence_max_segments_scanned: int = 5000
