"""Unit tests for the `demo-auto` API flows (B7).

The flows were originally written against the control API's route signatures rather than a
running control API, which hid four always-fail bugs: the `templates` analyze frame is a JSON
list and not an object, `DriftOut` names the id `drift_id`, issuing a source key needs a writer
role (so it cannot run while switched to the approver), and `ReplayOut` reports `published` and
`normalized` rather than `replayed`.

These tests drive the real flows over an httpx transport that answers like the control API, so
each of those four stays fixed. Anything the stub is not asked for is an error, which is what
makes a renamed route or a dropped call visible here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest
from demo_engine.auto import AutoRunner, FlowError
from demo_engine.scenario import load_scenario

from veyra_common.settings import Settings

SAMPLE = (
    '<134>Sep 26 14:05:09 fw01 app[233]: {"evt":"auth",'
    '"msg":"user=r.patil OK login from 10.4.1.20 via 10.2.3.4"} | trace='
)


def sse(frames: list[tuple[str, Any]]) -> bytes:
    out = b""
    for kind, data in frames:
        out += f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode()
    return out


class StubControl:
    """A control API that answers only what the flows are allowed to ask for."""

    def __init__(self, analyze_frames: list[tuple[str, Any]], drift: list[dict[str, Any]]) -> None:
        self.analyze_frames = analyze_frames
        self.drift = drift
        self.calls: list[str] = []
        self.user = "nobody"
        self.user_at_call: dict[str, str] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        key = f"{method} {path}"
        self.calls.append(key)
        self.user_at_call[key] = self.user
        body = json.loads(request.content) if request.content else {}

        if key == "POST /auth/login":
            self.user = body["email"]
            return httpx.Response(200, json={"email": self.user})
        if key == "POST /auth/demo-switch":
            self.user = body["email"]
            return httpx.Response(200, json={"email": self.user})
        if key == "GET /sources":
            return httpx.Response(200, json=[{"id": "src_authsrv_01"}])
        if key == "POST /onboarding/analyze":
            return httpx.Response(200, content=sse(self.analyze_frames))
        if key == "POST /onboarding/use-library":
            return httpx.Response(201, json={"contract_id": "authsrv", "version": 1})
        if path.startswith("/drafts/") and path.endswith("/submit"):
            return httpx.Response(200, json={"contract_id": "authsrv", "version": 1})
        if path.startswith("/drafts/") and method == "GET":
            return httpx.Response(200, json={"state": "ready", "templates": [{"id": "t1"}]})
        if key == "GET /drift":
            return httpx.Response(200, json=self.drift)
        if path.startswith("/drift/") and path.endswith("/draft"):
            return httpx.Response(200, json={"draft_id": "d-9"})
        if path.endswith("/approve") or path.endswith("/promote"):
            # The real four-eyes check lives in the control API; the stub only records who.
            return httpx.Response(200, json={})
        if key == "POST /sources/src_authsrv_01/keys":
            # WRITERS = admin, pack_author. The approver must not get here.
            if self.user not in ("admin@veyra", "author@maha"):
                return httpx.Response(403, json={"detail": f"{self.user} may not issue keys"})
            return httpx.Response(201, json={"key_id": "k-1", "secret": "s"})
        if key == "POST /replay":
            return httpx.Response(200, json={"job_id": "j-1"})
        if path.startswith("/replay/") and method == "GET":
            return httpx.Response(
                200, json={"state": "done", "total": 8, "published": 8, "normalized": 8}
            )
        return httpx.Response(599, json={"detail": f"the stub was not asked to answer {key}"})


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, metrics_port=0)


def runner(cfg: Settings, stub: StubControl) -> AutoRunner:
    scenario = load_scenario(name="sih_main", env={"VEYRA_DEMO_EPS_BASELINE": "15"})
    return AutoRunner(scenario, stage_runner=None, cfg=cfg, transport=stub.transport())  # type: ignore[arg-type]


def analyze_to_draft() -> list[tuple[str, Any]]:
    """The real frame order, including the one that is a list rather than an object."""
    return [
        ("classification", {"layers": ["syslog", "json"], "contract_id": "authsrv"}),
        ("templates", [{"template_sig": "sig-1", "count": 2, "tokens": ["user"]}]),
        ("library", {"matches": [], "matched": None}),
        ("draft", {"draft_id": "d-1"}),
        ("done", {"draft_id": "d-1"}),
    ]


def test_the_templates_frame_is_a_list_and_does_not_stop_the_flow(
    cfg: Settings, monkeypatch: Any
) -> None:
    stub = StubControl(analyze_to_draft(), drift=[])
    monkeypatch.setattr(AutoRunner, "_onboarding_samples", lambda self: [SAMPLE])
    detail = runner(cfg, stub).onboarding_flow()
    assert "authsrv@1" in detail
    assert "k-1" in detail


def test_the_key_is_issued_by_a_writer_not_the_approver(cfg: Settings, monkeypatch: Any) -> None:
    stub = StubControl(analyze_to_draft(), drift=[])
    monkeypatch.setattr(AutoRunner, "_onboarding_samples", lambda self: [SAMPLE])
    runner(cfg, stub).onboarding_flow()
    assert stub.user_at_call["POST /sources/src_authsrv_01/keys"] == "author@maha"
    # Four eyes still happened: the approver, not the author, approved.
    assert stub.user_at_call["POST /contracts/authsrv/versions/1/approve"] == "approver@veyra"


def test_a_matched_library_pack_adopts_the_pack_instead_of_waiting_for_a_draft(
    cfg: Settings, monkeypatch: Any
) -> None:
    frames = [
        ("classification", {"layers": ["syslog"], "contract_id": "authsrv"}),
        ("templates", [{"template_sig": "sig-1", "count": 1, "tokens": []}]),
        ("library", {"matches": [], "matched": "nginx_access"}),
        ("done", {"draft_id": None, "library": "nginx_access"}),
    ]
    stub = StubControl(frames, drift=[])
    monkeypatch.setattr(AutoRunner, "_onboarding_samples", lambda self: [SAMPLE])
    detail = runner(cfg, stub).onboarding_flow()
    assert "POST /onboarding/use-library" in stub.calls
    assert "library pack nginx_access" in detail


def test_an_analyze_error_frame_fails_the_flow(cfg: Settings, monkeypatch: Any) -> None:
    frames = [("error", {"message": "RefusedDraft: the drafter would have invented a field"})]
    stub = StubControl(frames, drift=[])
    monkeypatch.setattr(AutoRunner, "_onboarding_samples", lambda self: [SAMPLE])
    with pytest.raises(FlowError, match="RefusedDraft"):
        runner(cfg, stub).onboarding_flow()


def test_the_drift_item_is_read_by_drift_id(cfg: Settings) -> None:
    stub = StubControl([], drift=[{"drift_id": "dr-7", "template_sig": "sig-3", "state": "open"}])
    detail = runner(cfg, stub).drift_approve_promote_replay()
    assert "POST /drift/dr-7/draft" in stub.calls
    assert "authsrv@1 promoted" in detail


def test_the_replayed_count_comes_from_normalized_not_replayed(cfg: Settings) -> None:
    stub = StubControl([], drift=[{"drift_id": "dr-7", "template_sig": "sig-3", "state": "open"}])
    detail = runner(cfg, stub).drift_approve_promote_replay()
    assert "8 event(s) replayed" in detail


def test_no_open_drift_item_fails_rather_than_passing_quietly(cfg: Settings) -> None:
    stub = StubControl([], drift=[])
    with pytest.raises(FlowError, match="no open drift item"):
        runner(cfg, stub).drift_approve_promote_replay()


def test_a_missing_demo_source_is_registered_in_the_control_apis_own_words(
    cfg: Settings, monkeypatch: Any
) -> None:
    """`POST /sources` takes the API's transport names (`http_push`), not the envelope's
    (`http_hec_event`): the API answers 422 to the latter, which stopped the whole run."""
    routes = pytest.importorskip("control_api.routes_sources")
    stub = StubControl(analyze_to_draft(), drift=[])
    posted: list[dict[str, Any]] = []
    answer = stub.handle

    def without_the_source(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        if key == "GET /sources":
            return httpx.Response(200, json=[])
        if key == "POST /sources":
            posted.append(json.loads(request.content))
            return httpx.Response(201, json={"id": "src_authsrv_01"})
        return answer(request)

    monkeypatch.setattr(stub, "handle", without_the_source)
    monkeypatch.setattr(AutoRunner, "_onboarding_samples", lambda self: [SAMPLE])
    runner(cfg, stub).onboarding_flow()
    assert len(posted) == 1
    assert posted[0]["transport"] in get_args(routes.SourceTransport)
    assert posted[0]["transport"] == "http_push"
