"""The answers a drafter may give, as fixed lists (C4): one source for every backend.

Drafting is multiple choice. The tokens are already cut out by rules, so a model only says
which field each one fills, which class and activity the line is, and which constant an
enum path takes. These lists are what it chooses from: the Ollama client turns them into
its ``format`` schema, the decision client into its questions.

A token's fields follow from the **shape of its value**, not from its kind. Kinds are a
tokenizer's guess (``neel.k`` reads as a hostname and is a user name), but an address can
only fill an address field and a word can never be a port, which is what ``verify`` would
reject afterwards anyway.
"""

from __future__ import annotations

import ipaddress

from veyra_contracts.catalogue import CLASSES, ENUMS, FIELDS
from veyra_contracts.drafting.verify import IP_PATHS, PORT_PATHS
from veyra_engine import Token

MAX_PORT = 65535

# Answered by a constant from the enum, never by a token.
CONST_PATHS: tuple[str, ...] = tuple(sorted(ENUMS))
# Set by the contract itself: ``message`` is always the whole text, ``time`` comes from the
# time block, and ``action_id`` follows the activity.
_NOT_DRAFTED = frozenset({"message", "time", "action_id"})
_COUNT_PATHS = frozenset(
    {"process.pid", "http_response.code", "traffic.bytes_in", "traffic.bytes_out"}
)
# ``user.uid`` is a number on Linux and a string elsewhere, so it is offered to both shapes.
_EITHER = frozenset({"user.uid"})

TEXT_PATHS: tuple[str, ...] = tuple(
    sorted(FIELDS - IP_PATHS - PORT_PATHS - _COUNT_PATHS - frozenset(ENUMS) - _NOT_DRAFTED)
)
CLASS_ACTIVITIES: tuple[tuple[str, str], ...] = tuple(
    (cls.name, activity) for cls in CLASSES.values() for activity in cls.activities
)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def paths_for(token: Token) -> tuple[str, ...]:
    """The fields ``token`` may fill, sorted; never empty."""
    value = token.value
    if _is_ip(value):
        return tuple(sorted(IP_PATHS))
    if value.isascii() and value.isdigit():
        ports = PORT_PATHS if int(value) <= MAX_PORT else frozenset()
        return tuple(sorted(ports | _COUNT_PATHS | _EITHER))
    return TEXT_PATHS
