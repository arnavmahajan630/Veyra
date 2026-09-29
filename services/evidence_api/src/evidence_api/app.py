"""Evidence API (B4 prototype) — IF-API-EVIDENCE, ``/evidence/*``.

    GET  /health                          liveness
    GET  /evidence/{event_uid}            where the event is and what it hashes to
    GET  /evidence/{event_uid}/verify     the 8-step verification report
    GET  /evidence/roots                  the signed roots in the B3 ledger
    GET  /evidence/pubkey                 the Ed25519 public key
    POST /evidence/export/{event_uid}     the auditor's zip

Everything is read out of the vault and the ledger the earlier phases wrote, so this
service holds no state of its own. No authentication: that is the full B4's job.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse

from evidence_api.export import build_export
from evidence_api.locator import EvidenceNotFound, VaultLocator
from evidence_api.settings import EvidenceApiSettings
from evidence_api.verifier import Verifier
from veyra_evidence.keys import KeyProvider, get_key_provider

log = logging.getLogger(__name__)

# event_uid is a UUIDv7 string (IF-NAMING); reject anything else before touching the vault.
_UID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _check_uid(event_uid: str) -> str:
    if not _UID_RE.match(event_uid.strip()):
        raise HTTPException(status_code=400, detail=f"not an event_uid: {event_uid!r}")
    return event_uid.strip()


def create_app(cfg: EvidenceApiSettings | None = None, keys: KeyProvider | None = None) -> FastAPI:
    """Build the app. Tests pass their own ``cfg`` pointing at a temp vault."""
    settings_ = cfg or EvidenceApiSettings()
    provider = keys or get_key_provider(settings_)
    locator = VaultLocator(settings_, provider)
    verifier = Verifier(locator)

    api = FastAPI(
        title="VEYRA evidence API",
        version="0.1.0",
        description="Lineage and evidence endpoints (B4 prototype).",
    )
    api.state.cfg = settings_
    api.state.locator = locator
    api.state.verifier = verifier

    @api.get("/health")
    def health() -> dict[str, Any]:
        entries = locator.entries()
        return {
            "status": "ok",
            "service": settings_.service_name,
            "vault_dir": str(locator.vault_dir),
            "vault_exists": locator.vault_dir.is_dir(),
            "segments": len(locator.segment_paths()),
            "signed_roots": len(entries),
        }

    @api.get("/evidence/pubkey", response_class=PlainTextResponse)
    def pubkey() -> str:
        """The key window roots are signed with. Publishing it is the point."""
        return locator.public_key_pem()

    @api.get("/evidence/roots")
    def roots(limit: int = 50) -> dict[str, Any]:
        """The signed roots already in the ledger, newest first."""
        entries = locator.entries()
        newest = list(reversed(entries))[: max(1, min(limit, 1000))]
        return {
            "count": len(entries),
            "ledger": str(locator.ledger),
            "roots": [
                {
                    "window_id": entry.window_id,
                    "payload": entry.payload,
                    "sig_b64": entry.sig_b64,
                    "payload_sha256": entry.sha256,
                }
                for entry in newest
            ],
        }

    @api.get("/evidence/{event_uid}")
    def evidence(event_uid: str) -> dict[str, Any]:
        """Where the event lives, what it hashes to, and which root covers it."""
        uid = _check_uid(event_uid)
        try:
            return locator.locate(uid).summary()
        except EvidenceNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/evidence/{event_uid}/verify")
    def verify(event_uid: str) -> dict[str, Any]:
        """The 8 checks of IF-API-EVIDENCE. 200 even when a check fails: the body says so."""
        return verifier.verify(_check_uid(event_uid)).as_json()

    # The contract spells this one /evidence/verify/{event_uid}; both work.
    @api.get("/evidence/verify/{event_uid}")
    def verify_alias(event_uid: str) -> dict[str, Any]:
        return verifier.verify(_check_uid(event_uid)).as_json()

    @api.post("/evidence/export/{event_uid}")
    def export(event_uid: str) -> Response:
        """A zip that stands on its own: raw bytes, proof, signed root, verify.py."""
        uid = _check_uid(event_uid)
        report = verifier.verify(uid)
        if report.located is None:
            raise HTTPException(status_code=404, detail=f"no evidence for event {uid!r}")
        blob = build_export(report.located, report, locator)
        return Response(
            content=blob,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="veyra-evidence-{uid}.zip"',
                # Handy for a caller that wants the verdict without opening the zip.
                "X-Veyra-Verified": "true" if report.verified else "false",
            },
        )

    return api
