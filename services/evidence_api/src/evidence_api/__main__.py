"""evidence_api — lineage and evidence REST API (B4 prototype).

    python -m evidence_api

Serves the FastAPI app on ``VEYRA_API_PORT`` (8100, IF-PORTS), with /healthz and
/metrics added by ServiceApp alongside the evidence endpoints.
"""

from __future__ import annotations

import uvicorn

from evidence_api.app import create_app
from evidence_api.settings import EvidenceApiSettings
from veyra_common.service import ServiceApp
from veyra_evidence.keys import get_key_provider


def main() -> None:
    cfg = EvidenceApiSettings()
    # serve_http=False: uvicorn owns the port, ServiceApp only contributes the routes.
    app = ServiceApp(
        name=cfg.service_name, metrics_port=cfg.metrics_port, cfg=cfg, serve_http=False
    )
    keys = get_key_provider(cfg)
    keys.ensure_signing_key()
    api = create_app(cfg, keys)
    app.attach_fastapi(api)
    app.mark_ready()
    uvicorn.run(api, host=cfg.api_host, port=cfg.api_port, log_config=None)


if __name__ == "__main__":
    main()
