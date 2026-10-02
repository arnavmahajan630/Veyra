"""A decision model as the drafter (C4): multiple-choice questions instead of written JSON.

A decision model does not write. It reads a state plus typed questions and returns a
probability for each offered option, in one pass. Drafting is already that shape: one
question per variable token ("which field does this value fill?", over
``options.paths_for`` plus *none*), one for the class and activity, and one per drafted
enum path. Nothing outside the offered options can come back, so the closed vocabulary
(D10) holds by construction, and the probabilities say how sure each answer is.

The server is Ollaya (``POST /api/decide``), which speaks the same wire format for every
model it serves. ``DecisionClient`` satisfies ``drafter.Model``, so modes, the cache, the
heuristic cross-check and verify treat it like any other model.
"""

from __future__ import annotations

from typing import Any

import httpx

from veyra_contracts.catalogue import ENUMS
from veyra_contracts.drafting.heuristic import context_word
from veyra_contracts.drafting.ollama import WARM_TIMEOUT_S, DraftFailed
from veyra_contracts.drafting.options import CLASS_ACTIVITIES, paths_for
from veyra_contracts.drafting.request import Prepared
from veyra_contracts.drafting.schema import Confidence, DraftResponse

NONE = "none"
NOT_STATED = "not stated"
CLASS_QUESTION = "class"
# The enum paths a draft sets, like the heuristic drafter: severity stays with the engine's
# word list (A4) and the reviewer.
DRAFTED_CONSTS = ("status_id", "disposition_id")
HIGH, MEDIUM = 0.85, 0.6

# What each option means, in the model's terms. An option with no entry is offered by name.
FIELD_HINTS = {
    "src_endpoint.ip": "the address the action came from (the client or attacker)",
    "dst_endpoint.ip": "the address the action went to or through (the server, target or relay)",
    "device.ip": "the address of the device that wrote this log",
    "src_endpoint.port": "the port on the side the action came from",
    "dst_endpoint.port": "the port on the side the action went to (the service port)",
    "src_endpoint.hostname": "the host name the action came from",
    "dst_endpoint.hostname": "the host name the action went to",
    "device.hostname": "the host name of the device that wrote this log",
    "user.name": "the user the event is about (who logged in, failed, or was acted on)",
    "actor.user.name": "the user who performed an action on something else (e.g. ran sudo)",
    "user.uid": "a numeric or opaque id of the user",
    "user.domain": "the domain or realm of the user",
    "process.pid": "a process id",
    "process.name": "the name of a program or process",
    "process.cmd_line": "a full command line",
    "http_response.code": "an HTTP status code such as 200 or 404",
    "http_request.http_method": "an HTTP method such as GET or POST",
    "http_request.url.path": "the path of a requested URL",
    "traffic.bytes_in": "a count of bytes received",
    "traffic.bytes_out": "a count of bytes sent",
    "connection_info.protocol_name": "a network protocol name such as tcp or udp",
    "metadata.product.name": "the name of the product that wrote this log",
    "metadata.product.vendor_name": "the vendor of the product that wrote this log",
    "status_detail": "a short reason for the outcome",
    NONE: "none of these: a counter, an id or other detail with no field here",
}
CONST_QUESTIONS = {
    "status_id": "What was the outcome of the event in this log line?",
    "disposition_id": "Did a security control allow or block the traffic in this log line?",
}


def _where(prepared: Prepared, token_id: str) -> str:
    index = next(i for i, token in enumerate(prepared.tokens) if token.id == token_id)
    token = prepared.tokens[index]
    if token.key:
        return f'the value of "{token.key}"'
    word = context_word(prepared.tokens, index)
    return f'after the word "{word}"' if word else "at the start of the line"


def questions(prepared: Prepared) -> dict[str, dict[str, Any]]:
    """Every question of one draft, keyed by token id, ``class`` or enum path."""
    out: dict[str, dict[str, Any]] = {}
    for shown in prepared.request["tokens"]:
        options = [*paths_for(prepared.token(shown["id"])), NONE]
        out[shown["id"]] = {
            "type": "choice",
            "instructions": (
                f"In this log line, which field does the value {shown['value']!r} "
                f"({_where(prepared, shown['id'])}) fill?"
            ),
            "criteria": {option: FIELD_HINTS.get(option, option) for option in options},
        }
    out[CLASS_QUESTION] = {
        "type": "choice",
        "instructions": "What kind of event is this log line, and which activity?",
        "criteria": {
            f"{name}/{activity}": f"{name.replace('_', ' ')}: {activity}"
            for name, activity in CLASS_ACTIVITIES
        },
    }
    for path in DRAFTED_CONSTS:
        labels = [label for value, label in ENUMS[path].items() if value != 0]
        out[path] = {
            "type": "choice",
            "instructions": CONST_QUESTIONS[path],
            "criteria": {label: label for label in labels} | {NOT_STATED: "the line does not say"},
        }
    return out


def _ranked(answer: dict[str, Any], floor: float, stop: str) -> list[tuple[float, str]]:
    """An answer's options, best first, cut at ``stop`` or the first one under ``floor``."""
    out: list[tuple[float, str]] = []
    for option, probability in sorted(answer["probabilities"].items(), key=lambda kv: -kv[1]):
        if option == stop or probability < floor:
            break
        out.append((float(probability), option))
    return out


def assemble(
    prepared: Prepared, answers: dict[str, Any], *, min_probability: float
) -> DraftResponse:
    """Answers → an IF-LLM-DRAFT response. A path is mapped once: the surer token keeps it,
    the other takes its next option above ``min_probability`` or stays unmapped."""
    asked = questions(prepared)
    missing = sorted(set(asked) - set(answers))
    if missing:
        raise DraftFailed(f"no answer for {', '.join(missing)}")
    try:
        ranked = {
            shown["id"]: _ranked(answers[shown["id"]], min_probability, NONE)
            for shown in prepared.request["tokens"]
        }
        kept: dict[str, tuple[float, str]] = {}  # path -> (probability, token id)
        while any(ranked.values()):
            token_id = max((t for t in ranked if ranked[t]), key=lambda t: ranked[t][0][0])
            probability, path = ranked[token_id][0]
            if path in kept:
                ranked[token_id].pop(0)
                continue
            kept[path] = (probability, token_id)
            ranked[token_id] = []
        mappings: list[dict[str, Any]] = [
            {"ocsf_path": path, "token": token_id} for path, (_, token_id) in kept.items()
        ]
        probabilities = [probability for probability, _ in kept.values()]
        for path in DRAFTED_CONSTS:
            best = _ranked(answers[path], min_probability, NOT_STATED)[:1]
            for probability, label in best:
                const = next(value for value, name in ENUMS[path].items() if name == label)
                mappings.append({"ocsf_path": path, "const": const})
                probabilities.append(probability)
        chosen = answers[CLASS_QUESTION]
        name, activity = str(chosen["choice"]).split("/", 1)
        probabilities.append(float(chosen["probabilities"][chosen["choice"]]))
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise DraftFailed(f"the answer does not fit the questions: {exc!r}") from exc
    lowest = min(probabilities)
    confidence: Confidence = "high" if lowest >= HIGH else "medium" if lowest >= MEDIUM else "low"
    sure = ", ".join(f"{path} {probability:.2f}" for path, (probability, _) in kept.items())
    return DraftResponse.model_validate(
        {
            "class": name,
            "activity": activity,
            "confidence": confidence,
            "mappings": mappings,
            "rationale": f"decision model, probability per field: {sure}" if sure else "",
        }
    )


class DecisionClient:
    def __init__(
        self,
        url: str,
        model: str,
        *,
        timeout_s: float,
        keep_alive: str,
        min_probability: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._timeout_s = timeout_s
        self._keep_alive = keep_alive
        self._min_probability = min_probability
        self._client = client or httpx.Client(base_url=url, timeout=timeout_s)

    def _decide(self, body: dict[str, Any], *, timeout_s: float) -> httpx.Response:
        try:
            response = self._client.post("/api/decide", json=body, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise DraftFailed(f"the model timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise DraftFailed(f"decision server unavailable: {exc}") from exc
        if response.is_error:
            try:
                reason = response.json().get("error", response.text)
            except ValueError:
                reason = response.text
            raise DraftFailed(f"decision server said {response.status_code}: {reason}")
        return response

    def draft(self, prepared: Prepared) -> tuple[DraftResponse, str]:
        """One draft in one request: every question is answered in the same pass."""
        body = {
            "model": self.model,
            "state": prepared.request["samples_masked"][0],
            "questions": questions(prepared),
            "keep_alive": self._keep_alive,
        }
        response = self._decide(body, timeout_s=self._timeout_s)
        try:
            answers = response.json()["answers"]
        except (ValueError, KeyError, TypeError) as exc:
            raise DraftFailed("the answer is not a decision response") from exc
        return assemble(prepared, answers, min_probability=self._min_probability), response.text

    def warm(self) -> None:
        """Load the model and keep it resident (``make llm-warm``)."""
        body = {
            "model": self.model,
            "state": "ok",
            "questions": {"ok": {"type": "noul", "instructions": "Is this fine?"}},
            "keep_alive": self._keep_alive,
        }
        self._decide(body, timeout_s=WARM_TIMEOUT_S)

    def ps(self) -> list[dict[str, Any]]:
        """This model's loaded instances and their VRAM (``GET /api/ps``), for the bench."""
        response = self._client.get("/api/ps")
        response.raise_for_status()
        models: list[dict[str, Any]] = response.json().get("models", [])
        return [m for m in models if self.model in (m.get("name"), m.get("model"))]
