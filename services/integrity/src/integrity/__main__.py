"""integrity — sign and chain the vault's window roots (B3).

    python -m integrity

/healthz and /metrics on the metrics port (8204). Each pass signs every closed window
that is not in the ledger yet, so a restart resumes without duplicating a root.
"""

from __future__ import annotations

import logging

from integrity.integrity import Integrity, ensure_keys
from integrity.settings import IntegritySettings
from veyra_common.service import ServiceApp

log = logging.getLogger("integrity")


def main() -> None:
    cfg = IntegritySettings()
    app = ServiceApp(name=cfg.service_name, metrics_port=cfg.metrics_port, cfg=cfg)
    keys = ensure_keys(cfg)
    service = Integrity(cfg, keys)
    app.on_stop(service.stop)
    app.mark_ready()
    try:
        service.run()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
