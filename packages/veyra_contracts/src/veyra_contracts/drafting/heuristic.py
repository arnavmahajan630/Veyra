"""The deterministic drafter (C4 "Heuristic drafter"): rules over tokens, same output shape.

Used for ``LLM_MODE=heuristic``, when live and cache both fail, and as the cross-check
that flags a mapping "review" when the LLM disagrees with it.

- an IP after ``from|src|source|client`` → ``src_endpoint.ip``; after
  ``to|dst|via|dest|destination|server`` → ``dst_endpoint.ip``; otherwise first → src,
  second → dst;
- a port after ``port|spt|sport`` → ``src_endpoint.port``, after ``dpt|dport`` → dst;
- a user token → ``user.name``;
- ``FAILED|failure|denied|invalid|…`` → ``status_id`` 2, ``OK|success|accepted|…`` → 1;
- the class from the words and mappings (a stand-in for A4's ``class_hint``).
"""

from __future__ import annotations

from veyra_contracts.drafting.request import Prepared
from veyra_contracts.drafting.schema import DraftResponse, Mapping
from veyra_engine import Token

SRC_WORDS = frozenset({"from", "src", "source", "client", "rhost"})
DST_WORDS = frozenset({"to", "dst", "via", "dest", "destination", "server"})
SRC_PORT_WORDS = frozenset({"port", "spt", "sport", "srcport"})
DST_PORT_WORDS = frozenset({"dpt", "dport", "dstport"})
FAIL_WORDS = frozenset({"failed", "failure", "fail", "denied", "deny", "invalid", "error"})
OK_WORDS = frozenset({"ok", "success", "succeeded", "accepted", "allowed", "allow"})
AUTH_WORDS = frozenset(
    {"login", "logon", "logged", "auth", "authentication", "password", "session"}
)
LOGOFF_WORDS = frozenset({"logout", "logoff", "closed", "disconnect", "disconnected"})
NET_WORDS = frozenset({"traffic", "connection", "deny", "allow", "drop", "accept", "blocked"})


def context_word(tokens: list[Token], index: int) -> str:
    """The word that introduces token ``index``: its kv key, else the previous word."""
    token = tokens[index]
    if token.key:
        return token.key.lower()
    for previous in reversed(tokens[:index]):
        if previous.kind == "word":
            return previous.value.lower()
    return ""


def heuristic(prepared: Prepared) -> DraftResponse:
    tokens = prepared.tokens
    words = {t.value.lower() for t in tokens if t.kind == "word"}
    mappings: dict[str, Mapping] = {}

    def assign(path: str, token: Token) -> None:
        if path not in mappings:
            mappings[path] = Mapping(ocsf_path=path, token=token.id)

    ips: list[tuple[int, Token]] = []
    for index, token in enumerate(tokens):
        if token.id not in prepared.variable:
            continue
        context = context_word(tokens, index)
        if token.kind == "user":
            assign("user.name", token)
        elif token.kind in ("ip", "ipv6"):
            if context in SRC_WORDS:
                assign("src_endpoint.ip", token)
            elif context in DST_WORDS:
                assign("dst_endpoint.ip", token)
            else:
                ips.append((index, token))
        elif token.kind in ("int", "port", "kv_value") and token.value.isdigit():
            if context in SRC_PORT_WORDS:
                assign("src_endpoint.port", token)
            elif context in DST_PORT_WORDS:
                assign("dst_endpoint.port", token)
    for _, token in ips:  # unlabelled IPs fill src, then dst
        for path in ("src_endpoint.ip", "dst_endpoint.ip"):
            if path not in mappings:
                assign(path, token)
                break

    if words & FAIL_WORDS:
        mappings["status_id"] = Mapping(ocsf_path="status_id", const=2)
    elif words & OK_WORDS:
        mappings["status_id"] = Mapping(ocsf_path="status_id", const=1)

    if "user.name" in mappings or words & AUTH_WORDS:
        cls, activity = "authentication", "logoff" if words & LOGOFF_WORDS else "logon"
    elif "src_endpoint.ip" in mappings and ("dst_endpoint.ip" in mappings or words & NET_WORDS):
        cls, activity = "network_activity", "traffic"
    else:
        cls, activity = "base_event", "other"

    return DraftResponse.model_validate(
        {
            "class": cls,
            "activity": activity,
            "confidence": "low",
            "mappings": [m.model_dump(exclude_none=True) for m in mappings.values()],
            "rationale": "heuristic rules over token kinds and the words before them",
        }
    )
