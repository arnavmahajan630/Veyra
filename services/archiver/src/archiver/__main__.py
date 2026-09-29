"""archiver — raw Kafka events into the encrypted vault (B2).

    python -m archiver

/healthz and /metrics on the metrics port (8203). SIGTERM seals every open segment and
commits before exiting, so a clean stop leaves no events un-archived.
"""

from __future__ import annotations

import logging

from archiver.archiver import Archiver
from archiver.settings import ArchiverSettings
from veyra_common.service import ServiceApp
from veyra_evidence.keys import get_key_provider

log = logging.getLogger("archiver")


def main() -> None:
    cfg = ArchiverSettings()
    app = ServiceApp(name=cfg.service_name, metrics_port=cfg.metrics_port, cfg=cfg)
    keys = get_key_provider(cfg)
    keys.ensure_kek()  # fail fast if the vault key is unusable
    cfg.vault_dir.mkdir(parents=True, exist_ok=True)
    archiver = Archiver.create(cfg, keys)
    app.on_stop(archiver.stop)
    app.mark_ready()
    try:
        archiver.run()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
