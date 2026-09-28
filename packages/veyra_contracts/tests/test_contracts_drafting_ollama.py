"""The Ollama client (C4): schema-constrained chat, one retry, closed-vocabulary checks."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from veyra_common.framing import split_lines
from veyra_contracts.drafting.ollama import DraftFailed, OllamaClient
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


def test_the_prompt_is_small_and_its_hash_is_stable() -> None:
    first = messages(prepared().request)
    assert first[0]["role"] == "system" and first[-1]["role"] == "user"
    assert len(json.dumps(first)) < 10_000  # ~2.5k tokens (C4 target)
    assert prompt_sha(first) == prompt_sha(messages(prepared().request))
