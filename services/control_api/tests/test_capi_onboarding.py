"""Onboarding analyze (C4 AC4): pasted samples → groups → library match or a draft, streamed."""

from __future__ import annotations

import json
from pathlib import Path

from veyra_common.framing import split_lines

CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"


def lines(name: str, count: int) -> list[str]:
    return [f.raw.decode() for f in split_lines((CORPUS / name).read_bytes())][:count]


def events(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    out = []
    for frame in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        if "event" in fields:
            out.append((fields["event"], json.loads(fields["data"])))
    return out


def analyze(client, samples: list[str], source: str = "src_authsrv_01") -> list:  # type: ignore[no-untyped-def]
    response = client.post("/onboarding/analyze", json={"source_id": source, "samples": samples})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return events(response)


def test_t1_and_t2_become_two_templates_and_one_draft(client, authsrv_source) -> None:
    """C4 AC4: 2 templates, no library match, a draft ready (heuristic/cache: well under 1 s)."""
    stream = analyze(client, lines("authsrv_t1_ok.log", 3) + lines("authsrv_t2_session.log", 3))
    kinds = [kind for kind, _ in stream]
    assert kinds == ["classification", "templates", "library", "draft", "draft", "done"]
    classification = stream[0][1]
    assert classification["contract_id"] == "authsrv"
    assert classification["layers"][0] == {"syslog": {"variant": "auto"}}
    assert len(stream[1][1]) == 2 and stream[2][1]["matched"] is None
    draft = client.get(f"/drafts/{stream[-1][1]['draft_id']}").json()
    assert "version: 1" in draft["yaml"] and len(draft["templates"]) == 2
    assert draft["verification"]["ok"]


def test_sshd_samples_propose_the_library_pack(client, authsrv_source, ctx) -> None:
    stream = analyze(client, lines("linux_sshd.log", 24))
    library = dict(stream)["library"]
    assert library["matched"] == "linux_sshd"
    assert stream[-1] == ("done", {"draft_id": None, "library": "linux_sshd"})


def test_a_one_off_line_is_its_own_group(client, authsrv_source) -> None:
    samples = [*lines("authsrv_t1_ok.log", 3), "something nobody has seen before"]
    templates = dict(analyze(client, samples))["templates"]
    assert sorted(t["count"] for t in templates) == [1, 3]


def test_an_unknown_source_is_404(client, authsrv_source) -> None:
    body = {"source_id": "src_ghost_01", "samples": ["x"]}
    assert client.post("/onboarding/analyze", json=body).status_code == 404
