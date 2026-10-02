"""The archiver's own knobs, on top of the shared ones (IF-ENV).

Shared: ``VEYRA_SEGMENT_MAX_BYTES``, ``VEYRA_SEGMENT_MAX_SECONDS``, ``VEYRA_ZSTD_LEVEL``,
``VEYRA_DATA_DIR`` (which derives ``vault_dir``/``keys_dir``), ``VEYRA_KEY_PROVIDER``.
"""

from __future__ import annotations

from veyra_common.settings import ServiceSettings


class ArchiverSettings(ServiceSettings):
    service_name: str = "archiver"
    metrics_port: int = 8203  # IF-PORTS

    archive_group: str = "archiver"
    # A third seal trigger next to bytes and age, so a tiny-event stream still rolls over.
    segment_max_records: int = 10_000
    # New raw.<vendor> topics appear when a source is onboarded.
    archive_metadata_refresh_ms: int = 10_000
    # How long a seal waits for its IF-VAULT-INDEX records to be acknowledged. Short on
    # purpose: the evidence is already durable, and the index is a lookup shortcut, so a slow
    # broker must not hold up the next segment.
    archive_index_publish_timeout_s: float = 5.0
