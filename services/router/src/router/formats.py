"""How an event is rendered for a sink (IF-ROUTES ``format``).

``ocsf_json`` is the event exactly as IF-NORM-EVENT defines it, plus a flat ``veyra`` block, which
exists for two reasons — both about the SIEM rather than about us:

* **Wazuh rules can match it.** A rule field name is a JSON path, and matching nested values is
  where `same_field` correlation gets fragile — so the fields the rules key on are flattened here,
  rather than the rules working around the event's shape (IF-WAZUH).
* **An analyst can read it.** `veyra.tier` beside the event says "this one was guessed at" without
  anyone having to learn what `ulpf.conformance` means.

Every value comes straight off ``ulpf``, which is complete on every event including tier 4.
"""

from __future__ import annotations

from typing import Any

# The flat field the brute-force rule can correlate on. Whether Wazuh's `same_field` works with a
# nested name is the one thing A6 has to establish by experiment on the pinned version, and a flat
# name is the fallback — so it is always written, and the rule uses whichever works.
SRC_IP_FIELD = "src_ip"


def ocsf_json(event: dict[str, Any]) -> dict[str, Any]:
    """The event plus ``veyra.{tier,class,tenant,source,revision,src_ip}``."""
    ulpf = event.get("ulpf") or {}
    contract = ulpf.get("contract") or {}
    out = dict(event)
    veyra: dict[str, Any] = {
        "tier": int(ulpf.get("tier", 4)),
        "class": int(event.get("class_uid", 0)),
        "tenant": str(ulpf.get("tenant_id", "")),
        "source": str(ulpf.get("source_id", "")),
        "revision": int(ulpf.get("revision", 1)),
    }
    if contract.get("id"):
        veyra["contract"] = f"{contract['id']}@{contract.get('version', 1)}"
    source_ip = _dig(event, "src_endpoint.ip")
    if source_ip:
        veyra[SRC_IP_FIELD] = str(source_ip)
    out["veyra"] = veyra
    return out


FORMATS = {"ocsf_json": ocsf_json}


def render(name: str, event: dict[str, Any]) -> dict[str, Any]:
    formatter = FORMATS.get(name)
    if formatter is None:
        raise ValueError(f"unknown format {name!r}; known: {', '.join(sorted(FORMATS))}")
    return formatter(event)


def _dig(event: dict[str, Any], path: str) -> Any:
    cursor: Any = event
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor
