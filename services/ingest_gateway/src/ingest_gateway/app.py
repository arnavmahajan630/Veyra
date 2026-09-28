"""The gateway's HTTP surface: HEC raw, HEC event, batch upload.

Everything here is deliberately Splunk-shaped, down to the error bodies (`{"text": …, "code": …}`),
because the promise in Beat 2 is "point your existing shipper at this and change nothing else". A
client that already speaks HEC must not be able to tell the difference until it looks at the
response of `/v1/batch`, which is VEYRA's own.

Order of work per request, and each step is a different status code:

1. authenticate (401), 2. read the body under the size cap (413), 3. parse it (400),
4. spend quota (429), 5. stamp envelopes, 6. produce and wait for Kafka (503), 7. 200.

Stamping happens *before* producing and never after, because P1 says the envelope is created before
anything else looks at the bytes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram

from ingest_gateway.hec import HecFormatError, events_from_body
from ingest_gateway.producer import DeliveryFailed, RawProducer
from ingest_gateway.quota import QuotaLimiter
from ingest_gateway.registry import AuthFailure, KeyRegistry, Principal
from ingest_gateway.settings import GatewaySettings
from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.hashing import sha256_hex
from veyra_common.models import Envelope, HecMeta
from veyra_common.models.envelope import FramingMethod, Transport

log = logging.getLogger(__name__)

ACCEPTED = Counter("veyra_gateway_accepted_total", "Events accepted", ["source", "endpoint"])
THROTTLED = Counter("veyra_gateway_throttled_total", "Events rejected by quota", ["source"])
REJECTED = Counter("veyra_gateway_rejected_total", "Requests rejected", ["reason"])
LATENCY = Histogram(
    "veyra_gateway_request_seconds",
    "Time to stamp and durably produce one request",
    ["endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# HEC's own error codes, so a Splunk client's own error handling still works.
HEC_INVALID_AUTH = 3
HEC_INVALID_DATA = 6
HEC_SERVER_BUSY = 9
HEC_INTERNAL = 8


class GatewayContext:
    """Everything a request handler needs, assembled once at startup."""

    def __init__(
        self,
        *,
        cfg: GatewaySettings,
        registry: KeyRegistry,
        producer: RawProducer,
        limiter: QuotaLimiter | None = None,
        collector_id: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self.producer = producer
        self.limiter = limiter or QuotaLimiter()
        self.collector_id = collector_id or f"gateway-{cfg.instance}"
        self.acks = 0


router = APIRouter()


def get_ctx(request: Request) -> GatewayContext:
    return request.app.state.ctx  # type: ignore[no-any-return]


def hec_error(status: int, text: str, code: int, *, headers: dict[str, str] | None = None) -> Any:
    return JSONResponse({"text": text, "code": code}, status_code=status, headers=headers)


def bearer_secret(request: Request) -> str:
    """The secret from ``Authorization: Splunk <secret>`` or ``Bearer <secret>``."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() in {"splunk", "bearer"} and value.strip():
        return value.strip()
    # Some HEC clients send the token as a query parameter; accept it, since refusing would look
    # like an auth failure for a client that is in fact configured correctly.
    return str(request.query_params.get("token", "")).strip()


def authenticate(request: Request, ctx: GatewayContext = Depends(get_ctx)) -> Principal:
    """401 on anything unauthenticated, 503 while the registry is still catching up."""
    if not ctx.registry.ready:
        # Answering 401 here would be a lie: we do not know yet whether the key is good, and a
        # client that treats 401 as fatal would stop retrying a perfectly valid key.
        REJECTED.labels("not_ready").inc()
        raise GatewayResponse(
            hec_error(503, "Server is starting: control topic not read yet", HEC_SERVER_BUSY)
        )
    try:
        return ctx.registry.resolve(bearer_secret(request))
    except AuthFailure as exc:
        REJECTED.labels("auth").inc()
        log.warning("auth rejected", extra={"reason": exc.reason, "path": request.url.path})
        raise GatewayResponse(hec_error(401, "Invalid authorization", HEC_INVALID_AUTH)) from exc


class GatewayResponse(Exception):
    """Carries a ready-made response out of a dependency or helper."""

    def __init__(self, response: Any) -> None:
        super().__init__("gateway response")
        self.response = response


async def read_body(request: Request, ctx: GatewayContext) -> bytes:
    """The request body, refusing anything over the configured cap.

    ``Content-Length`` is checked first because it is free, and the accumulated size is checked as
    well: a chunked request has no length, and a cap that only trusts the header is not a cap.
    """
    limit = ctx.cfg.gateway_max_body_bytes
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise GatewayResponse(
            hec_error(413, f"Request body exceeds {limit} bytes", HEC_INVALID_DATA)
        )
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise GatewayResponse(
                hec_error(413, f"Request body exceeds {limit} bytes", HEC_INVALID_DATA)
            )
        chunks.append(chunk)
    return b"".join(chunks)


def stamp_events(
    ctx: GatewayContext,
    principal: Principal,
    *,
    transport: Transport,
    items: list[tuple[bytes, FramingMethod, int, bool, HecMeta | None]],
    custody: str = "realtime",
    peer_ip: str | None = None,
) -> list[Envelope]:
    """Stamp each (bytes, framing, parts, truncated, hec_meta) tuple into an IF-ENVELOPE."""
    envelopes: list[Envelope] = []
    for raw, framing_method, parts, truncated, meta in items:
        envelopes.append(
            stamp(
                raw,
                collector_id=ctx.collector_id,
                transport=transport,
                framing_method=framing_method,
                zone=principal.zone,  # type: ignore[arg-type]
                tenant_id=principal.tenant_id,
                source_id=principal.source_id,
                vendor=principal.vendor,
                peer_ip=peer_ip,
                parts=parts,
                truncated=truncated,
                custody=custody,  # type: ignore[arg-type]
                auth_method="api_key",
                auth_key_id=principal.key_id,
                max_event_bytes=ctx.cfg.max_event_bytes,
                hec_meta=meta,
            )
        )
    return envelopes


def deliver(ctx: GatewayContext, envelopes: list[Envelope], *, endpoint: str) -> Any | None:
    """Produce durably. Returns a response to send on failure, or ``None`` on success."""
    try:
        with LATENCY.labels(endpoint).time():
            ctx.producer.send_all(envelopes)
    except DeliveryFailed as exc:
        # No partial ack, ever: the client must be free to retry the whole request.
        REJECTED.labels("kafka").inc()
        log.error("delivery failed", extra={"events": len(envelopes), "error": str(exc)})
        return hec_error(503, f"Not durably accepted: {exc}", HEC_SERVER_BUSY)
    except Exception as exc:  # a broken producer must not leak a stack trace to a shipper
        REJECTED.labels("internal").inc()
        log.exception("unexpected produce failure")
        return hec_error(503, f"Internal error: {type(exc).__name__}", HEC_INTERNAL)
    for envelope in envelopes:
        ACCEPTED.labels(envelope.source_id, endpoint).inc()
    return None


def spend_quota(ctx: GatewayContext, principal: Principal, count: int) -> Any | None:
    """Returns a 429 response when the source is over its quota, else ``None``."""
    if ctx.limiter.allow(principal.source_id, principal.quota_eps, cost=count):
        return None
    THROTTLED.labels(principal.source_id).inc(count)
    REJECTED.labels("quota").inc()
    return hec_error(
        429,
        f"Source {principal.source_id} is over its quota of {principal.quota_eps} EPS",
        HEC_SERVER_BUSY,
        headers={"Retry-After": "1"},
    )


def success(ctx: GatewayContext, count: int) -> Any:
    ctx.acks += 1
    return JSONResponse({"text": "Success", "code": 0, "ackId": ctx.acks, "events": count})


# ---------------------------------------------------------------------- endpoints
@router.post("/services/collector/raw")
async def collector_raw(request: Request, ctx: GatewayContext = Depends(get_ctx)) -> Response:
    """HEC raw: a text body, split with the same rule the edge uses (`framing.split_lines`)."""
    principal = authenticate(request, ctx)
    body = await read_body(request, ctx)
    events = split_lines(body, max_event_bytes=ctx.cfg.max_event_bytes)
    if not events:
        REJECTED.labels("empty").inc()
        return hec_error(400, "No data", HEC_INVALID_DATA)

    throttled = spend_quota(ctx, principal, len(events))
    if throttled is not None:
        return throttled

    envelopes = stamp_events(
        ctx,
        principal,
        transport="http_hec_raw",
        items=[
            (
                event.raw,
                "multiline_join" if event.parts > 1 else "newline",
                event.parts,
                event.truncated,
                None,
            )
            for event in events
        ],
        peer_ip=request.client.host if request.client else None,
    )
    failure = deliver(ctx, envelopes, endpoint="raw")
    return failure or success(ctx, len(envelopes))


@router.post("/services/collector/event")
@router.post("/services/collector")
async def collector_event(request: Request, ctx: GatewayContext = Depends(get_ctx)) -> Response:
    """HEC event: one or more JSON objects, with or without separators between them."""
    principal = authenticate(request, ctx)
    body = await read_body(request, ctx)
    try:
        events = events_from_body(body.decode("utf-8", errors="replace"))
    except HecFormatError as exc:
        REJECTED.labels("format").inc()
        return hec_error(400, str(exc), HEC_INVALID_DATA)

    throttled = spend_quota(ctx, principal, len(events))
    if throttled is not None:
        return throttled

    envelopes = stamp_events(
        ctx,
        principal,
        transport="http_hec_event",
        items=[(event.raw, "http_body", 1, False, event.meta) for event in events],
        peer_ip=request.client.host if request.client else None,
    )
    failure = deliver(ctx, envelopes, endpoint="event")
    return failure or success(ctx, len(envelopes))


@router.post("/v1/batch")
async def batch_upload(
    request: Request, file: UploadFile, ctx: GatewayContext = Depends(get_ctx)
) -> Response:
    """A post-hoc upload: every line is an event, and custody says it was not live.

    The manifest is the point of this endpoint. `sha256_of_file` is over the uploaded bytes exactly
    as received, so the uploader can prove later which file it sent, and the first/last event_uid
    bound the range in lineage without needing to list every event.
    """
    principal = authenticate(request, ctx)
    limit = ctx.cfg.gateway_max_body_bytes
    payload = await file.read()
    if len(payload) > limit:
        REJECTED.labels("too_large").inc()
        return hec_error(413, f"File exceeds {limit} bytes", HEC_INVALID_DATA)
    events = split_lines(payload, max_event_bytes=ctx.cfg.max_event_bytes)
    if not events:
        REJECTED.labels("empty").inc()
        return hec_error(400, "No data", HEC_INVALID_DATA)

    throttled = spend_quota(ctx, principal, len(events))
    if throttled is not None:
        return throttled

    envelopes = stamp_events(
        ctx,
        principal,
        transport="http_batch",
        items=[(event.raw, "batch_line", event.parts, event.truncated, None) for event in events],
        custody="post_hoc",
        peer_ip=request.client.host if request.client else None,
    )
    failure = deliver(ctx, envelopes, endpoint="batch")
    if failure is not None:
        return failure
    ctx.acks += 1
    return JSONResponse(
        {
            "text": "Success",
            "code": 0,
            "ackId": ctx.acks,
            "manifest": {
                "count": len(envelopes),
                "sha256_of_file": sha256_hex(payload),
                "filename": file.filename,
                "first_event_uid": envelopes[0].event_uid,
                "last_event_uid": envelopes[-1].event_uid,
                "custody": "post_hoc",
            },
        }
    )


def create_app(ctx: GatewayContext) -> FastAPI:
    app = FastAPI(title="VEYRA ingest-gateway", version="0.1.0")
    app.state.ctx = ctx
    app.include_router(router)

    @app.exception_handler(GatewayResponse)
    async def _carry(_request: Request, exc: GatewayResponse) -> Any:
        return exc.response

    return app
