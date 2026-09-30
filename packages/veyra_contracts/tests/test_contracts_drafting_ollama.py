"""The Ollama client (C4): schema-constrained chat, one retry, closed-vocabulary checks."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from veyra_common.framing import split_lines
from veyra_contracts.drafting.ollama import WARM_TIMEOUT_S, DraftFailed, OllamaClient
from veyra_contracts.drafting.prompt import messages, prompt_sha
from veyra_contracts.drafting.request import build

CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"
GOOD = {
    "class": "authentication",
    "activity": "logon",
    "confidence": "high",
    "mappings": [
        {"ocsf_path": "user.name", "token": "k2"},
        {"ocsf_path": "src_endpoint.ip", "token": "k6"},
        {"ocsf_path": "dst_endpoint.ip", "token": "k8"},
        {"ocsf_path": "status_id", "const": 2},
    ],
    "rationale": "user, source, relay; FAILED is a failure",
}


def prepared():  # type: ignore[no-untyped-def]
    raws = [f.raw for f in split_lines((CORPUS / "authsrv_t3_failed.log").read_bytes())][:8]
    return build(raws, template_sig="t_3c85a1bfbf81")


def client_answering(*contents: str, seen: list[dict] | None = None) -> OllamaClient:
    replies = list(contents)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": replies.pop(0)}}
        )

    http = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return OllamaClient(
        "http://ollama",
        "qwen2.5:3b",
        num_ctx=4096,
        timeout_s=25,
        keep_alive="30m",
        client=http,
    )


def test_a_valid_answer_is_used_as_is() -> None:
    seen: list[dict] = []
    response, raw = client_answering(json.dumps(GOOD), seen=seen).draft(prepared())
    assert response.dump()["mappings"][0] == {"ocsf_path": "user.name", "token": "k2"}
    call = seen[0]
    assert call["model"] == "qwen2.5:3b" and call["stream"] is False
    assert call["options"] == {"temperature": 0, "seed": 7, "num_ctx": 4096}
    assert call["keep_alive"] == "30m"
    assert call["format"]["properties"]["class"]["type"] == "string"
    assert json.loads(raw) == GOOD


def test_an_invalid_answer_is_retried_once_with_the_complaint() -> None:
    bad = GOOD | {"mappings": [{"ocsf_path": "user.name", "token": "k99"}]}
    seen: list[dict] = []
    response, _ = client_answering(json.dumps(bad), json.dumps(GOOD), seen=seen).draft(prepared())
    assert len(seen) == 2 and "k99" in seen[1]["messages"][-1]["content"]
    assert response.mappings[0].token == "k2"


def test_two_bad_answers_fail() -> None:
    with pytest.raises(DraftFailed, match="not valid JSON"):
        client_answering("not json", "still not json").draft(prepared())


def test_a_timeout_fails_fast() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    http = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(slow))
    client = OllamaClient(
        "http://ollama", "m", num_ctx=4096, timeout_s=1, keep_alive="30m", client=http
    )
    with pytest.raises(DraftFailed, match="timed out"):
        client.draft(prepared())


def timed_client(handler, timeout_s: float) -> OllamaClient:  # type: ignore[no-untyped-def]
    http = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return OllamaClient(
        "http://ollama", "m", num_ctx=4096, timeout_s=timeout_s, keep_alive="30m", client=http
    )


def test_the_retry_only_gets_what_is_left_of_the_timeout() -> None:
    budgets: list[float] = []
    replies = ["not json", json.dumps(GOOD)]

    def handler(request: httpx.Request) -> httpx.Response:
        budgets.append(request.extensions["timeout"]["read"])
        time.sleep(0.3)
        return httpx.Response(200, json={"message": {"content": replies.pop(0)}})

    timed_client(handler, timeout_s=2).draft(prepared())
    assert budgets[0] == pytest.approx(2, abs=0.05)
    assert budgets[1] == pytest.approx(1.7, abs=0.1)


def test_no_retry_once_the_timeout_is_spent() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        time.sleep(0.25)
        return httpx.Response(200, json={"message": {"content": "not json"}})

    with pytest.raises(DraftFailed, match="no time left"):
        timed_client(handler, timeout_s=0.2).draft(prepared())
    assert len(calls) == 1


def test_warming_outlasts_the_draft_timeout() -> None:
    seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"response": "ok"})

    http = httpx.Client(
        base_url="http://ollama", timeout=25, transport=httpx.MockTransport(handler)
    )
    OllamaClient(
        "http://ollama", "m", num_ctx=4096, timeout_s=25, keep_alive="30m", client=http
    ).warm()
    assert seen == [WARM_TIMEOUT_S] and WARM_TIMEOUT_S > 25


def test_ps_reports_only_this_model() -> None:
    loaded = [
        {"name": "qwen2.5:3b", "model": "qwen2.5:3b", "size_vram": 2},
        {"name": "llama3.2:3b", "model": "llama3.2:3b", "size_vram": 3},
    ]
    http = httpx.Client(
        base_url="http://ollama",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": loaded})),
    )
    client = OllamaClient(
        "http://ollama", "llama3.2:3b", num_ctx=4096, timeout_s=25, keep_alive="30m", client=http
    )
    assert [m["size_vram"] for m in client.ps()] == [3]


def test_the_prompt_is_small_and_its_hash_is_stable() -> None:
    first = messages(prepared().request)
    assert first[0]["role"] == "system" and first[-1]["role"] == "user"
    assert len(json.dumps(first)) < 10_000  # ~2.5k tokens (C4 target)
    assert prompt_sha(first) == prompt_sha(messages(prepared().request))
