"""Test data shared by the C2 test files: the authsrv contract versions and T3 events.

Not a test module (no ``test_`` prefix); test files import it directly, because pytest
puts this directory on ``sys.path``.
"""

from __future__ import annotations

from pathlib import Path

from control_api.evidence import EventRef

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.models import Envelope

CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"
T3_SIG = "t_3c85a1bfbf81"  # IF-TEMPLATE-SIG vector: scope authsrv, the FAILED login shape

SOURCE = {
    "id": "src_authsrv_01",
    "tenant_id": "t_maha_power",
    "name": "Auth Server",
    "vendor": "custom",
    "zone": "dmz",
    "transport": "http_push",
}

V1 = """\
contract: authsrv
version: 1
tenant: t_maha_power
sources: [src_authsrv_01]
envelope:
  - syslog: {variant: auto}
  - json: {text_field: msg}
templates:
  - id: auth_ok
    pattern: 'user=<user> OK login from <src_ip:ip> via <dst_ip:ip>'
    class: authentication
    activity: logon
    map:
      user.name: $user
      src_endpoint.ip: $src_ip
      dst_endpoint.ip: $dst_ip
      status_id: {const: 1}
required: [time, user.name]
pii: [user.name, src_endpoint.ip]
"""

FAILED = """\
  - id: auth_failed
    pattern: 'user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>'
    class: authentication
    activity: logon
    map:
      user.name: $user
      src_endpoint.ip: $src_ip
      dst_endpoint.ip: $dst_ip
      status_id: {const: 2}
    unmapped: [attempts]
"""


def version(n: int) -> str:
    """authsrv@n; from v2 on it also covers the FAILED login (Beat 4)."""
    text = V1.replace("version: 1", f"version: {n}")
    return text if n == 1 else text.replace("required:", FAILED + "required:")


def submit(client, text: str) -> dict:  # type: ignore[no-untyped-def]
    response = client.post("/contracts", json={"yaml": text})
    assert response.status_code == 201, response.text
    body: dict = response.json()
    return body


def activate(client, as_user, n: int) -> None:  # type: ignore[no-untyped-def]
    """Submit authsrv@n as the author, approve and promote it as the approver."""
    as_user("author@maha")
    submit(client, version(n))
    as_user("approver@veyra")
    assert client.post(f"/contracts/authsrv/versions/{n}/approve").status_code == 200
    assert client.post(f"/contracts/authsrv/versions/{n}/promote").status_code == 200


def t3_events(count: int = 8) -> list[tuple[EventRef, Envelope]]:
    """The first ``count`` T3 lines of the corpus as stored events at revision 1."""
    lines = [f.raw for f in split_lines((CORPUS / "authsrv_t3_failed.log").read_bytes())]
    out = []
    for i, raw in enumerate(lines[:count]):
        uid = f"0192a4f0-0000-7000-8000-{i:012d}"
        envelope = stamp(
            raw, collector_id="edge-dmz-01", transport="http_hec_event",
            framing_method="http_body", tenant_id="t_maha_power", source_id="src_authsrv_01",
            vendor="custom", event_uid=uid, received_time="2026-09-26T14:10:00.000000000Z",
        )  # fmt: skip
        ref = EventRef(uid, "src_authsrv_01", 1, 4, "raw.custom", 0, 100 + i)
        out.append((ref, envelope))
    return out
