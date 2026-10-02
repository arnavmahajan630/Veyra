"""The decision-model drafter (C4): questions out, probabilities in, an IF-LLM-DRAFT response."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from veyra_common.framing import split_lines
from veyra_common.settings import Settings
from veyra_contracts.drafting.backends import make_client
from veyra_contracts.drafting.cache import DraftCache
from veyra_contracts.drafting.decision import (
    CLASS_QUESTION,
    NONE,
    NOT_STATED,
    DecisionClient,
    assemble,
    questions,
)
from veyra_contracts.drafting.drafter import Drafter
from veyra_contracts.drafting.ollama import WARM_TIMEOUT_S, DraftFailed, OllamaClient
from veyra_contracts.drafting.options import CLASS_ACTIVITIES, paths_for
from veyra_contracts.drafting.request import Prepared, build
from veyra_contracts.drafting.schema import problems

CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"
T3_SIG = "t_3c85a1bfbf81"
# T3: user=<k2> FAILED login from <k6> via <k8> attempts:<k10>
REVIEWED = {
    "k2": "user.name",
    "k6": "src_endpoint.ip",
    "k8": "dst_endpoint.ip",
    "k10": NONE,
    CLASS_QUESTION: "authentication/logon",
    "status_id": "Failure",
    "disposition_id": NOT_STATED,
}


def t3() -> Prepared:
    raws = [f.raw for f in split_lines((CORPUS / "authsrv_t3_failed.log").read_bytes())][:8]
    return build(raws, template_sig=T3_SIG)


def answers(prepared: Prepared, picks: dict[str, Any]) -> dict[str, Any]:
    """A server reply. ``picks[question]`` is the winning option (given 0.9) or a full
    ``{option: probability}``; the other options share what is left."""
    out: dict[str, Any] = {}
    for name, question in questions(prepared).items():
        options = list(question["criteria"])
        pick = picks[name]
        given = pick if isinstance(pick, dict) else {pick: 0.9}
        rest = [o for o in options if o not in given]
        share = (1 - sum(given.values())) / len(rest) if rest else 0.0
        probabilities = {o: given.get(o, share) for o in options}
        choice = max(probabilities, key=lambda o: probabilities[o])
        out[name] = {
            "type": "choice",
            "choice": choice,
            "confidence": 0.8,
            "probabilities": probabilities,
        }
    return out


def client_answering(reply: Any, *, status: int = 200, seen: list[dict] | None = None):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append({"path": request.url.path, "body": json.loads(request.content)})
        return httpx.Response(status, json=reply)

    http = httpx.Client(base_url="http://ollaya", transport=httpx.MockTransport(handler))
    return DecisionClient(
        "http://ollaya",
        "laya:en",
        timeout_s=25,
        keep_alive="30m",
        min_probability=0.5,
        client=http,
    )


def mapped(response: Any) -> dict[str, Any]:
    return {m.ocsf_path: m.token if m.token is not None else m.const for m in response.mappings}


# ---------------------------------------------------------------- questions
def test_one_question_per_variable_token_over_its_fields_plus_none() -> None:
    prepared = t3()
    asked = questions(prepared)
    assert set(asked) == {"k2", "k6", "k8", "k10", CLASS_QUESTION, "status_id", "disposition_id"}
    for token_id in ("k2", "k6", "k8", "k10"):
        options = list(asked[token_id]["criteria"])
        assert options == [*paths_for(prepared.token(token_id)), NONE]
        assert asked[token_id]["type"] == "choice"
    assert list(asked["k6"]["criteria"])[:3] == ["device.ip", "dst_endpoint.ip", "src_endpoint.ip"]


def test_a_question_says_where_the_value_sits_and_hides_the_person() -> None:
    asked = questions(t3())
    assert "'103.21.4.77'" in asked["k6"]["instructions"]
    assert 'after the word "from"' in asked["k6"]["instructions"]
    assert 'after the word "via"' in asked["k8"]["instructions"]
    assert 'the value of "user"' in asked["k2"]["instructions"]
    # Personal values are masked in what the model sees, like the LLM request.
    assert "<USER_1>" in asked["k2"]["instructions"]
    assert "a.sharma" not in json.dumps(asked)


def test_class_and_enum_questions_offer_the_catalogue() -> None:
    asked = questions(t3())
    assert len(asked[CLASS_QUESTION]["criteria"]) == len(CLASS_ACTIVITIES) == 19
    assert "authentication/logon" in asked[CLASS_QUESTION]["criteria"]
    assert list(asked["status_id"]["criteria"]) == ["Success", "Failure", "Other", NOT_STATED]
    assert list(asked["disposition_id"]["criteria"]) == ["Allowed", "Blocked", NOT_STATED]
    assert "severity_id" not in asked  # left to the engine's word list and the reviewer


# ---------------------------------------------------------------- one request
def test_a_draft_is_one_request_with_the_masked_sample_as_state() -> None:
    prepared, seen = t3(), []
    client = client_answering({"answers": answers(prepared, REVIEWED)}, seen=seen)
    response, raw = client.draft(prepared)
    assert len(seen) == 1 and seen[0]["path"] == "/api/decide"
    body = seen[0]["body"]
    assert body["model"] == "laya:en" and body["keep_alive"] == "30m"
    assert body["state"] == prepared.request["samples_masked"][0]
    assert "a.sharma" not in body["state"]
    assert body["questions"] == questions(prepared)
    assert json.loads(raw)["answers"]["k6"]["choice"] == "src_endpoint.ip"
    assert response.class_ == "authentication" and response.activity == "logon"


def test_the_reviewed_answers_become_the_reviewed_t3_draft() -> None:
    prepared = t3()
    response = assemble(prepared, answers(prepared, REVIEWED), min_probability=0.5)
    assert mapped(response) == {
        "user.name": "k2",
        "src_endpoint.ip": "k6",
        "dst_endpoint.ip": "k8",
        "status_id": 2,
    }
    assert response.confidence == "high"  # every kept answer is at 0.9
    assert "src_endpoint.ip 0.90" in response.rationale


# ---------------------------------------------------------------- assembly rules
def test_none_and_not_stated_map_nothing() -> None:
    prepared = t3()
    picks = REVIEWED | {"k2": NONE, "status_id": NOT_STATED}
    response = assemble(prepared, answers(prepared, picks), min_probability=0.5)
    assert "user.name" not in mapped(response) and "status_id" not in mapped(response)


def test_an_answer_under_the_threshold_is_left_out() -> None:
    prepared = t3()
    picks = REVIEWED | {"k8": {"dst_endpoint.ip": 0.45, "device.ip": 0.3}}
    low = assemble(prepared, answers(prepared, picks), min_probability=0.5)
    assert "dst_endpoint.ip" not in mapped(low)
    assert (
        mapped(assemble(prepared, answers(prepared, picks), min_probability=0.4))["dst_endpoint.ip"]
        == "k8"
    )


def test_two_tokens_on_one_path_the_surer_keeps_it_and_the_other_takes_its_next() -> None:
    prepared = t3()
    picks = REVIEWED | {
        "k6": {"src_endpoint.ip": 0.7, "dst_endpoint.ip": 0.2},
        "k8": {"src_endpoint.ip": 0.52, "dst_endpoint.ip": 0.4},
    }
    response = assemble(prepared, answers(prepared, picks), min_probability=0.3)
    assert mapped(response)["src_endpoint.ip"] == "k6"
    assert mapped(response)["dst_endpoint.ip"] == "k8"
    assert problems(response, {"k2", "k6", "k8", "k10"}) == []


def test_the_loser_stays_unmapped_when_its_next_option_is_too_unsure() -> None:
    """What Laya really answered for T3: both addresses look like the source."""
    prepared = t3()
    picks = REVIEWED | {
        "k6": {"src_endpoint.ip": 0.72, "dst_endpoint.ip": 0.07, "device.ip": 0.06},
        "k8": {"src_endpoint.ip": 0.53, "dst_endpoint.ip": 0.15, "device.ip": 0.21},
    }
    response = assemble(prepared, answers(prepared, picks), min_probability=0.5)
    assert mapped(response)["src_endpoint.ip"] == "k6"
    assert "k8" not in mapped(response).values()


def test_a_next_option_is_not_taken_past_none() -> None:
    prepared = t3()
    picks = REVIEWED | {
        "k6": {"src_endpoint.ip": 0.7},
        "k8": {"src_endpoint.ip": 0.5, NONE: 0.3, "dst_endpoint.ip": 0.2},
    }
    response = assemble(prepared, answers(prepared, picks), min_probability=0.1)
    assert "k8" not in mapped(response).values()


@pytest.mark.parametrize(
    ("lowest", "level"), [(0.9, "high"), (0.85, "high"), (0.7, "medium"), (0.55, "low")]
)
def test_confidence_is_the_least_sure_kept_answer(lowest: float, level: str) -> None:
    prepared = t3()
    picks = REVIEWED | {"k6": {"src_endpoint.ip": lowest}}
    assert assemble(prepared, answers(prepared, picks), min_probability=0.5).confidence == level


def test_no_reply_can_leave_the_closed_vocabulary() -> None:
    """Whatever wins each question, the draft passes the checks a written answer must pass."""
    prepared = t3()
    asked = questions(prepared)
    token_ids = {t["id"] for t in prepared.request["tokens"]}
    names = list(asked)
    firsts = [list(asked[n]["criteria"])[:3] for n in names]
    for combo in itertools.islice(itertools.product(*firsts), 400):
        response = assemble(
            prepared, answers(prepared, dict(zip(names, combo, strict=True))), min_probability=0.5
        )
        assert problems(response, token_ids) == []


# ---------------------------------------------------------------- failures
def test_a_missing_answer_fails_the_draft() -> None:
    prepared = t3()
    partial = answers(prepared, REVIEWED)
    del partial["k8"]
    with pytest.raises(DraftFailed, match="no answer for k8"):
        client_answering({"answers": partial}).draft(prepared)


def test_a_reply_that_is_not_a_decision_response_fails() -> None:
    with pytest.raises(DraftFailed, match="not a decision response"):
        client_answering({"message": "hello"}).draft(t3())
    prepared = t3()
    broken = answers(prepared, REVIEWED)
    broken[CLASS_QUESTION] = {"type": "choice", "choice": "nonsense", "probabilities": {}}
    with pytest.raises(DraftFailed, match="does not fit the questions"):
        client_answering({"answers": broken}).draft(prepared)


def test_a_server_error_carries_its_reason() -> None:
    reply = {"error": 'model "laya:en" not found, try pulling it first', "code": "MODEL_NOT_FOUND"}
    with pytest.raises(DraftFailed, match=r"404: model .* not found"):
        client_answering(reply, status=404).draft(t3())


def test_a_timeout_and_an_unreachable_server_fail() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for handler, message in ((slow, "timed out"), (down, "unavailable")):
        http = httpx.Client(base_url="http://ollaya", transport=httpx.MockTransport(handler))
        client = DecisionClient(
            "http://ollaya", "m", timeout_s=1, keep_alive="30m", min_probability=0.5, client=http
        )
        with pytest.raises(DraftFailed, match=message):
            client.draft(t3())


def test_a_failing_decision_model_falls_back_like_any_model(tmp_path: Path) -> None:
    client = client_answering({"error": "queue full", "code": "QUEUE_FULL"}, status=503)
    outcome = Drafter(mode="live_then_cache", cache=DraftCache(tmp_path), client=client).draft(t3())
    assert outcome.source == "heuristic"
    assert any("503" in note for note in outcome.notes)


def test_a_live_decision_draft_is_badged_and_cross_checked(tmp_path: Path) -> None:
    prepared = t3()
    wrong = REVIEWED | {"k8": "device.ip"}
    client = client_answering({"answers": answers(prepared, wrong)})
    outcome = Drafter(mode="live", cache=DraftCache(tmp_path), client=client).draft(prepared)
    assert outcome.source == "llm:laya:en"
    assert outcome.review == []  # the heuristic maps k8 to another path, not this path elsewhere
    agreed = client_answering({"answers": answers(prepared, REVIEWED | {"k6": "dst_endpoint.ip"})})
    disputed = Drafter(mode="live", cache=DraftCache(tmp_path), client=agreed).draft(prepared)
    assert "dst_endpoint.ip" in disputed.review


# ---------------------------------------------------------------- warm, ps, backend choice
def test_warming_and_ps_use_the_servers_endpoints() -> None:
    seen: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.extensions["timeout"]["read"]))
        if request.url.path == "/api/ps":
            return httpx.Response(
                200, json={"models": [{"name": "laya:en", "size_vram": 5}, {"name": "other"}]}
            )
        return httpx.Response(200, json={"answers": {"ok": {"type": "noul", "noul": 0.9}}})

    http = httpx.Client(base_url="http://ollaya", transport=httpx.MockTransport(handler))
    client = DecisionClient(
        "http://ollaya", "laya:en", timeout_s=5, keep_alive="30m", min_probability=0.5, client=http
    )
    client.warm()
    assert seen[0] == ("/api/decide", WARM_TIMEOUT_S)
    assert client.ps() == [{"name": "laya:en", "size_vram": 5}]


def test_the_backend_comes_from_settings_and_a_prefix_overrides_it() -> None:
    assert isinstance(make_client(Settings()), OllamaClient)
    chosen = make_client(Settings(llm_backend="decision", decision_model="laya:en"))
    assert isinstance(chosen, DecisionClient) and chosen.model == "laya:en"
    prefixed = make_client(Settings(), model="decision:winnow:e4b")
    assert isinstance(prefixed, DecisionClient) and prefixed.model == "winnow:e4b"
    named = make_client(Settings(), model="llama3.2:3b")
    assert isinstance(named, OllamaClient) and named.model == "llama3.2:3b"
