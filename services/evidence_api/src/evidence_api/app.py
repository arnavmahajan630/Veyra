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

import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse

from evidence_api import console
from evidence_api.export import build_export
from evidence_api.locator import EvidenceNotFound, VaultLocator
from evidence_api.settings import EvidenceApiSettings
from evidence_api.verifier import Verifier
from veyra_evidence.keys import KeyProvider, get_key_provider

log = logging.getLogger(__name__)

# Memoised ledger audit, keyed by the ledger's tail. Cleared whenever the tail moves, so it
# holds one entry and can never serve a verdict for a ledger that has since changed.
_audit_cache: dict[tuple[Any, ...], Any] = {}

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

    def _audit_by_window(entries: list[Any]) -> dict[str, Any]:
        """Run B3's ledger audit and index its findings by window id.

        Returns ``{window_id: {signature_ok, prev_link_ok, window_order_ok, chain_ok},
        "__status__": "PASS"|"FAIL"}``. An audit that cannot run leaves the flags off
        entirely, so the console shows "unknown" rather than a green it did not earn.
        """
        if not entries:
            return {"__status__": "PASS"}
        # The audit re-verifies every signature in the ledger. The SSE stream asks for it
        # twice every two seconds, so the answer is memoised against the ledger's current
        # tail: a new signed root changes the key and the audit runs again.
        try:
            stat = Path(locator.ledger).stat()
            fingerprint = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            fingerprint = (0, 0)
        # The fingerprint is the ledger file itself, not just its tail: an edited entry in
        # the middle must not be answered from a cached PASS. That is exactly what the
        # `root_rewrite` tamper does.
        cache_key = (len(entries), entries[-1].sha256, fingerprint)
        cached = _audit_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            import sys

            root_dir = str(Path(__file__).resolve().parents[4])
            if root_dir not in sys.path:
                sys.path.insert(0, root_dir)
            from tools.ledger_audit import audit as ledger_audit

            pem = provider.public_key_pem(provider.signing_key_id)
            report = ledger_audit(entries, pem, str(locator.ledger))
        except Exception as exc:
            log.warning("ledger audit unavailable: %s", exc)
            return {"__status__": "unknown"}

        checks = {
            "signature": "signature_ok",
            "prev_signed_sha256": "prev_link_ok",
            "window_order": "window_order_ok",
        }
        by_window: dict[str, Any] = {"__status__": report.status}
        for finding in report.findings:
            key = checks.get(finding.check)
            if key is None:
                continue
            row = by_window.setdefault(finding.window_id, {})
            row[key] = finding.status == "PASS"
        for window, row in by_window.items():
            if window != "__status__":
                row["chain_ok"] = all(row.values())
        _audit_cache.clear()
        _audit_cache[cache_key] = by_window
        return by_window

    def _attach_raw_bytes(payload: dict[str, Any], event_uid: str) -> None:
        """Fill ``raw.raw_text``/``raw.raw_b64`` from the vault (IF-API-EVIDENCE).

        The lineage index stores metadata only, but the event-detail response is
        contracted to carry the bytes themselves — the raw pane highlights byte spans
        against them. Best effort: an event whose segment cannot be read keeps ``None``
        and the console shows the pane as unavailable rather than inventing text.
        """
        raw = payload.get("raw")
        if not isinstance(raw, dict) or raw.get("raw_text") is not None:
            return
        try:
            located = locator.locate(event_uid)
        except Exception:
            return
        blob = located.record.envelope_bytes
        raw["raw_text"] = blob.decode("utf-8", errors="replace")
        raw["raw_b64"] = base64.b64encode(blob).decode("ascii")

    api = FastAPI(
        title="VEYRA evidence API",
        version="0.1.0",
        description="Lineage and evidence endpoints (B4 prototype).",
    )
    api.state.cfg = settings_
    api.state.locator = locator
    api.state.verifier = verifier

    # ClickHouse is probed lazily and retried. Probing once at startup meant an evidence API
    # that booted before the indexer had migrated stayed on the degraded path forever — every
    # panel on the console then showed fallback data for the rest of the run.
    _ch_state: dict[str, Any] = {"client": None, "next_try": 0.0}
    _CH_RETRY_S = 10.0

    def clickhouse() -> Any:
        if _ch_state["client"] is not None:
            return _ch_state["client"]
        if not (getattr(settings_, "ch_url", "") or getattr(settings_, "clickhouse_url", "")):
            return None
        if time.monotonic() < float(_ch_state["next_try"]):
            return None
        _ch_state["next_try"] = time.monotonic() + _CH_RETRY_S
        try:
            from veyra_lineage.client import make_client as make_ch_client

            candidate = make_ch_client(settings_, connect_timeout=1)
            candidate.command("SELECT 1")
        except Exception as exc:
            log.debug("ClickHouse not reachable yet: %s", exc)
            return None
        log.info("ClickHouse is reachable; serving lineage from the index")
        _ch_state["client"] = candidate
        return candidate

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

    # ------------------------------------------------ Evidence endpoints
    @api.get("/pubkey", response_class=PlainTextResponse)
    @api.get("/evidence/pubkey", response_class=PlainTextResponse)
    def pubkey() -> str:
        """The key window roots are signed with. Publishing it is the point."""
        return locator.public_key_pem()

    @api.get("/roots")
    @api.get("/evidence/roots")
    def roots(limit: int = 50) -> dict[str, Any]:
        """The signed roots already in the ledger, newest first, with audit state.

        The per-root ``signature_ok``/``prev_link_ok`` flags come from B3's ledger audit,
        so the Evidence page's chain strip turns a bead red from the same rules
        ``make ledger-audit`` applies — one definition of "the chain is broken".
        """
        entries = locator.entries()
        audit = _audit_by_window(entries)
        newest = list(reversed(entries))[: max(1, min(limit, 1000))]
        return {
            "count": len(entries),
            "ledger": str(locator.ledger),
            "audit_status": audit.get("__status__", "unknown"),
            "roots": [
                {
                    "window_id": entry.window_id,
                    "payload": entry.payload,
                    "sig_b64": entry.sig_b64,
                    "payload_sha256": entry.sha256,
                    **audit.get(entry.window_id, {}),
                }
                for entry in newest
            ],
        }

    @api.get("/evidence/{event_uid}")
    @api.get(
        "/{event_uid:regex(^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$)}"
    )
    def evidence(event_uid: str) -> dict[str, Any]:
        """Where the event lives, what it hashes to, and which root covers it."""
        uid = _check_uid(event_uid)
        try:
            return locator.locate(uid).summary()
        except EvidenceNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/verify/{event_uid}")
    @api.get("/{event_uid}/verify")
    @api.get("/evidence/verify/{event_uid}")
    @api.get("/evidence/{event_uid}/verify")
    def verify(event_uid: str) -> dict[str, Any]:
        """The 8 checks of IF-API-EVIDENCE. 200 even when a check fails: the body says so."""
        return verifier.verify(_check_uid(event_uid)).as_json()

    @api.post("/export/{event_uid}")
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

    # ------------------------------------------------ Lineage endpoints
    @api.get("/search")
    @api.get("/lineage/search")
    def lineage_search(q: str = "", tenant: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Find events by event_uid, sha prefix, template_sig, IP, or user."""
        q = q.strip()
        if not q:
            return {"q": "", "matched_on": None, "hits": []}
        ch = clickhouse()
        if ch is not None:
            try:
                from veyra_lineage import queries as lineage_queries

                res = lineage_queries.search(q, tenant=tenant, limit=limit, client=ch)
                return res.model_dump()
            except Exception as exc:
                log.warning("lineage search query failed: %s", exc)
        # Fallback using vault scan if uid or sha matches
        try:
            located = locator.locate(q)
            env = located.envelope
            raw_text = located.record.envelope_bytes.decode("utf-8", errors="replace")
            preview = raw_text[:120].replace("\n", " ")
            return {
                "q": q,
                "matched_on": "event_uid",
                "hits": [
                    {
                        "event_uid": located.event_uid,
                        "tenant_id": env.get("tenant_id", "*"),
                        "source_id": env.get("source_id", "unknown"),
                        "received_time": env.get("received_time"),
                        "revision": 1,
                        "tier": 1,
                        "raw_sha256": env.get("raw_sha256", ""),
                        "raw_preview": preview,
                    }
                ],
            }
        except Exception:
            return {"q": q, "matched_on": None, "hits": []}

    @api.get("/events/{event_uid}")
    @api.get("/lineage/events/{event_uid}")
    def lineage_event_detail(event_uid: str) -> dict[str, Any]:
        """One event's full lineage: envelope, revisions, vault location, receipts."""
        uid = _check_uid(event_uid)
        ch = clickhouse()
        if ch is not None:
            try:
                from veyra_lineage import queries as lineage_queries

                detail = lineage_queries.event_detail(uid, client=ch)
                if detail is not None:
                    payload = detail.model_dump()
                    _attach_raw_bytes(payload, uid)
                    return payload
            except Exception as exc:
                log.warning("lineage event_detail query failed: %s", exc)
        # Fallback from vault if available
        try:
            located = locator.locate(uid)
            env = located.envelope
            raw_bytes = located.record.envelope_bytes
            raw_text = raw_bytes.decode("utf-8", errors="replace")
            raw_preview = raw_text[:120].replace("\n", " ")
            return {
                "event_uid": uid,
                "raw_ref": located.header.get("raw_ref")
                or {
                    "topic": located.header.get("topic", "raw.custom"),
                    "partition": located.header.get("partition", 0),
                    "offset": located.record.offset,
                },
                "raw": {
                    "raw_ref": {
                        "topic": located.header.get("topic", "raw.custom"),
                        "partition": located.header.get("partition", 0),
                        "offset": located.record.offset,
                    },
                    "raw_sha256": env.get("raw_sha256", ""),
                    "raw_len": env.get("raw_len", len(raw_bytes)),
                    "received_time": env.get("received_time", ""),
                    "tenant_id": env.get("tenant_id", "*"),
                    "source_id": env.get("source_id", ""),
                    "vendor": env.get("vendor", ""),
                    "zone": env.get("zone", ""),
                    "collector_id": env.get("collector_id", ""),
                    "transport": env.get("transport", "http_push"),
                    "listener": env.get("listener"),
                    "peer_ip": env.get("peer_ip"),
                    "custody": env.get("custody", "realtime"),
                    "auth_method": env.get("auth_method", "token"),
                    "framing_method": env.get("framing_method", "json"),
                    "framing_truncated": env.get("framing_truncated", False),
                    "framing_parts": env.get("framing_parts", 1),
                    "raw_preview": raw_preview,
                    "raw_text": raw_text,
                    "raw_b64": base64.b64encode(raw_bytes).decode("ascii"),
                },
                # No revisions: the vault holds the raw bytes, not the normalized forms.
                # Inventing one here would put an OCSF body on screen that no normalizer
                # ever produced, so the console renders "index unavailable" instead.
                "revisions": [],
                "index_available": False,
                "vault": {
                    "segment_id": located.segment_id,
                    "record_idx": located.record.record_idx,
                    "chain_hash": located.record.chain_hash_hex,
                    "sealed": True,
                    "sealed_at": located.header.get("sealed_at", ""),
                    "window_id": located.ledger_entry.window_id if located.ledger_entry else None,
                },
                # Delivery receipts live in the index, not the vault. The two "delivered"
                # rows this used to invent were shown on the Lineage page as real delivery
                # history for events that may never have been routed at all.
                "receipts": [],
                "dlq": [],
                "shadow": [],
            }
        except EvidenceNotFound as exc:
            raise HTTPException(status_code=404, detail=f"event {uid!r} not found") from exc

    def _chain_ok() -> bool | None:
        """The ledger audit's verdict, or None when it could not be run."""
        audit = _audit_by_window(locator.entries())
        status = audit.get("__status__", "unknown")
        if status == "unknown":
            return None
        return bool(status == "PASS")

    @api.get("/overview")
    @api.get("/lineage/overview")
    def lineage_overview(tenant: str | None = None) -> dict[str, Any]:
        """Overview metrics for the console Overview page.

        Both paths return the console's shape, built by `evidence_api.console`. They used to
        return different shapes, and the console only understood the fallback's.
        """
        ch = clickhouse()
        if ch is not None:
            try:
                from veyra_lineage import queries as lineage_queries

                model = lineage_queries.overview(tenant=tenant, client=ch)
                return console.overview_payload(
                    model,
                    dimensions=lineage_queries.source_dimensions(tenant=tenant, client=ch),
                    route_rates=lineage_queries.route_rates(client=ch),
                    history=lineage_queries.tier_history(tenant=tenant, client=ch),
                    chain_ok=_chain_ok(),
                )
            except Exception as exc:
                log.warning("lineage overview query failed: %s", exc)

        # No index to ask: real zeros plus what the vault itself knows. Never invented counts.
        entries = locator.entries()
        last_root = None
        if entries:
            last = entries[-1]
            last_root = {
                "window_id": last.window_id,
                "window_end": last.payload.get("window_end") or None,
                "immudb_verified": False,
            }
        return console.empty_overview_payload(
            segments=len(locator.segment_paths()),
            last_sealed_at=(entries[-1].payload.get("window_end") or None) if entries else None,
            last_root=last_root,
            chain_ok=_chain_ok(),
        )

    @api.get("/sources")
    @api.get("/lineage/sources")
    def lineage_sources(tenant: str | None = None) -> list[dict[str, Any]]:
        """Source health metrics for the console Sources page."""
        ch = clickhouse()
        if ch is not None:
            try:
                from veyra_lineage import queries as lineage_queries

                return console.source_health_payload(
                    lineage_queries.source_health(tenant=tenant, client=ch),
                    dimensions=lineage_queries.source_dimensions(tenant=tenant, client=ch),
                )
            except Exception as exc:
                log.warning("lineage sources query failed: %s", exc)
        return []

    @api.get("/templates/{sig}/events")
    @api.get("/lineage/templates/{sig}/events")
    def lineage_template_events(sig: str, limit: int = 50) -> list[dict[str, Any]]:
        """Events carrying a template signature, for replay and backtest."""
        ch = clickhouse()
        if ch is not None:
            try:
                from veyra_lineage import queries as lineage_queries

                return [
                    e.model_dump()
                    for e in lineage_queries.template_events(sig, limit=limit, client=ch)
                ]
            except Exception as exc:
                log.warning("lineage template_events query failed: %s", exc)
        return []

    @api.get("/stream")
    @api.get("/lineage/stream")
    async def lineage_stream(request: Request, tenant: str | None = None) -> StreamingResponse:
        """SSE stream pushing overview ticks and newly signed roots.

        Two event types: ``overview`` every 2 s, and ``root`` once per window as the
        integrity service seals it. The Evidence page's ledger table live-appends from
        ``root``, so it never has to poll.
        """

        async def _generator():
            # Both of these are synchronous and slow — `overview` runs ClickHouse queries and
            # `roots` runs the whole ledger audit — so they go to a thread. Called directly
            # they blocked the event loop for every other request every two seconds.
            first = await asyncio.to_thread(roots, 200)
            seen: set[str] = {r["window_id"] for r in first["roots"]}
            try:
                while not await request.is_disconnected():
                    overview_data = await asyncio.to_thread(lineage_overview, tenant)
                    payload = json.dumps(overview_data)
                    yield f"event: overview\ndata: {payload}\n\n"
                    latest = await asyncio.to_thread(roots, 50)
                    for root in reversed(latest["roots"]):
                        if root["window_id"] in seen:
                            continue
                        seen.add(root["window_id"])
                        yield f"event: root\ndata: {json.dumps(root)}\n\n"
                    await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                pass

        return StreamingResponse(_generator(), media_type="text/event-stream")

    return api
