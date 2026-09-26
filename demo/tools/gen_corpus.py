"""Write demo/corpus/* — the sample logs every phase tests against (S0.4 task 19).

Deterministic on purpose: golden tests (A3), tier-3 goldens (A4), the drift cluster
(C3) and the demo scenario (B7) all key off these exact bytes. Regenerate with
``python demo/tools/gen_corpus.py`` and commit the diff if you intend to change them.

The three authsrv templates are verbatim from docs/plan/04_DEMO_SCRIPT.md §2.1:
T1 and T2 go into the onboarding samples, T3 does not — that is what makes T3 drift.
"""

from __future__ import annotations

from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "corpus"

USERS = ["r.patil", "a.sharma", "neel.k", "s.iyer", "v.rao", "admin", "root", "d.mehta"]
ATTACKER = "45.12.3.9"
ATTACKER_2 = "103.21.4.77"


def linux_sshd() -> str:
    """RFC 3164 sshd lines: failures (incl. the brute-force burst), accepts, invalid users."""
    lines: list[str] = []
    second = 0

    def ts() -> str:
        nonlocal second
        second += 1
        return f"Sep 26 14:0{second // 60}:{second % 60:02d}"

    # A brute-force burst from one IP: six failures inside a minute, which is what
    # Wazuh rule 100111 (frequency 5 / 60 s on the same src_endpoint.ip) must catch.
    for i in range(6):
        lines.append(
            f"<86>{ts()} core-lnx-07 sshd[{4410 + i}]: Failed password for invalid user admin "
            f"from {ATTACKER} port {52144 + i} ssh2"
        )
    lines += [
        f"<86>{ts()} core-lnx-07 sshd[4500]: Invalid user oracle from {ATTACKER} port 52200",
        f"<86>{ts()} core-lnx-07 sshd[4501]: Failed password for root from 10.4.1.66 port 41022 ssh2",
        f"<86>{ts()} core-lnx-07 sshd[4502]: Accepted password for r.patil from 10.4.1.20 port 51002 ssh2",
        f"<86>{ts()} core-lnx-07 sshd[4503]: pam_unix(sshd:session): session opened for user r.patil by (uid=0)",
        f"<86>{ts()} core-lnx-07 sshd[4503]: Received disconnect from 10.4.1.20 port 51002:11: disconnected by user",
        f"<86>{ts()} core-lnx-07 sshd[4503]: pam_unix(sshd:session): session closed for user r.patil",
        f"<86>{ts()} core-lnx-07 sshd[4510]: Accepted publickey for s.iyer from 10.4.1.31 port 51110 ssh2: RSA SHA256:7Yb2",
        f"<86>{ts()} core-lnx-07 sshd[4511]: Failed password for invalid user postgres from 192.168.9.14 port 33440 ssh2",
        f"<86>{ts()} core-lnx-07 sshd[4512]: error: maximum authentication attempts exceeded for root from {ATTACKER} port 52300 ssh2 [preauth]",
        f"<86>{ts()} core-lnx-07 sshd[4513]: Disconnecting invalid user admin {ATTACKER} port 52300: Too many authentication failures",
        f"<86>{ts()} core-lnx-07 sshd[4520]: Accepted password for v.rao from 10.4.2.9 port 40122 ssh2",
        f"<86>{ts()} core-lnx-07 sshd[4521]: Connection closed by authenticating user d.mehta 10.4.2.11 port 40130 [preauth]",
        f"<86>{ts()} core-lnx-07 sudo[4530]: r.patil : TTY=pts/0 ; PWD=/home/r.patil ; USER=root ; COMMAND=/usr/bin/systemctl restart veyra",
        f"<86>{ts()} core-lnx-07 sshd[4531]: Failed none for invalid user admin from {ATTACKER_2} port 60011 ssh2",
        f"<86>{ts()} core-lnx-07 sshd[4532]: Accepted password for neel.k from 10.4.1.44 port 52901 ssh2",
        f"<86>{ts()} core-lnx-07 sshd[4533]: pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost={ATTACKER}  user=admin",
        f"<86>{ts()} core-lnx-07 sshd[4534]: Server listening on 0.0.0.0 port 22.",
        f"<86>{ts()} core-lnx-07 sshd[4535]: Received signal 15; terminating.",
    ]
    return "\n".join(lines) + "\n"


def acme_ngfw_cef() -> str:
    """CEF:0 traffic allow/deny from the 'Acme NGFW' vendor (library contract source)."""
    rows = [
        (100, "traffic deny", 5, ATTACKER, "10.2.3.4", 22, "deny", "tcp", 0, 0),
        (100, "traffic deny", 5, ATTACKER, "10.2.3.4", 3389, "deny", "tcp", 0, 0),
        (101, "traffic allow", 3, "10.4.1.20", "10.2.3.9", 443, "allow", "tcp", 8421, 19022),
        (101, "traffic allow", 3, "10.4.1.21", "10.2.3.9", 443, "allow", "tcp", 1200, 4400),
        (100, "traffic deny", 5, ATTACKER_2, "10.2.3.4", 445, "deny", "tcp", 0, 0),
        (101, "traffic allow", 3, "10.4.2.9", "8.8.8.8", 53, "allow", "udp", 74, 190),
        (102, "threat blocked", 8, ATTACKER, "10.2.3.4", 80, "block", "tcp", 0, 0),
        (101, "traffic allow", 3, "10.4.1.44", "10.2.4.10", 8080, "allow", "tcp", 3310, 9900),
        (100, "traffic deny", 5, "192.168.9.14", "10.2.3.4", 23, "deny", "tcp", 0, 0),
        (101, "traffic allow", 3, "10.4.1.31", "10.2.3.20", 22, "allow", "tcp", 990, 2210),
    ]
    lines: list[str] = []
    for i, (sig, name, sev, src, dst, dpt, act, proto, bin_, bout) in enumerate(rows * 2):
        stamp = f"Sep 26 2026 14:05:{i % 60:02d}"
        lines.append(
            f"CEF:0|Acme|NGFW|9.1|{sig}|{name}|{sev}|rt={stamp} src={src} dst={dst} dpt={dpt} "
            f"proto={proto} act={act} in={bin_} out={bout} deviceInboundInterface=eth1 "
            f"cs1Label=zone cs1=dmz"
        )
    return "\n".join(lines) + "\n"


T1 = (
    '<134>Sep 26 14:05:09 fw01 app[233]: {{"evt":"auth","msg":"user={user} OK login '
    'from {src} via 10.2.3.4"}} | trace='
)
T2 = (
    '<134>Sep 26 14:05:10 fw01 app[233]: {{"evt":"session","msg":"session {sid} closed '
    'for {user} after {secs}s"}}'
)
T3_HEAD = (
    '<134>Sep 26 14:05:11 fw01 app[233]: {{"evt":"auth","msg":"user={user} FAILED login '
    'from {src} via 10.2.3.4 attempts:{n}"}} | trace='
)
T3_TRACE = "  at com.x.Auth.login(Auth.java:88)"


def authsrv_t1_ok() -> str:
    """Template 1 — successful logins. Goes into the onboarding samples (Beat 2)."""
    srcs = ["10.4.1.20", "10.4.1.21", "10.4.2.9", "10.4.1.31", "10.4.1.44"]
    lines = [T1.format(user=USERS[i % len(USERS)], src=srcs[i % len(srcs)]) for i in range(22)]
    return "\n".join(lines) + "\n"


def authsrv_t2_session() -> str:
    """Template 2 — session close. Also in the onboarding samples."""
    lines = [
        T2.format(sid=7781 + i, user=USERS[i % len(USERS)], secs=312 + i * 7) for i in range(22)
    ]
    return "\n".join(lines) + "\n"


def authsrv_t3_failed() -> str:
    """Template 3 — the shape onboarding never saw, so it drifts (Beat 3 -> Beat 4).

    Every event is two physical lines: the JSON line plus a stack-trace continuation.
    The edge must join them into ONE envelope (framing multiline_join, parts=2).
    """
    lines: list[str] = []
    for i in range(8):  # exactly the 8 events the demo replays
        lines.append(T3_HEAD.format(user="a.sharma", src=ATTACKER_2, n=i % 3 + 1))
        lines.append(T3_TRACE)
    for i in range(6):  # a few more, from other users, so the cluster is realistic
        lines.append(T3_HEAD.format(user=USERS[i % len(USERS)], src="10.4.9.2", n=1))
        lines.append(T3_TRACE)
    return "\n".join(lines) + "\n"


def ot_historian() -> str:
    """Legacy OT historian: fixed-width, semicolon-separated, with a Devanagari tag.

    Unregistered on purpose (Beat 3 shows it as tier 3). A4's AC2 verifies that field
    offsets are correct across the multi-byte Hindi field.
    """
    rows = [
        ("TAG0001", "टर्बाइन-1 दबाव", "OK", 12.4, "bar"),
        ("TAG0002", "टर्बाइन-1 तापमान", "HIGH", 91.2, "degC"),
        ("TAG0003", "पंप-2 प्रवाह", "OK", 320.0, "lpm"),
        ("TAG0004", "वाल्व-7 स्थिति", "FAULT", 0.0, "pct"),
        ("TAG0005", "जनरेटर लोड", "OK", 812.5, "MW"),
        ("TAG0006", "बस वोल्टेज", "LOW", 219.4, "kV"),
        ("TAG0007", "शीतलक स्तर", "OK", 74.1, "pct"),
    ]
    lines: list[str] = []
    for i, (tag, label, state, value, unit) in enumerate(rows * 3):
        ts = f"26-09-2026 14:05:{i % 60:02d}"
        lines.append(
            f"{ts};HIST01  ;{tag:<8};{label};{state:<6};{value:>10.3f};{unit:<5};"
            f"SEQ{i:05d};OPR=maint01;SITE=MAHA-PUNE-1"
        )
    return "\n".join(lines) + "\n"


def garbage_bin() -> bytes:
    """Everything that must not crash the engine (A3 AC3, A4 fuzz).

    Contents, in order: invalid UTF-8, a NUL run, a 1-byte line, an empty line, a
    200 KB single line, a truncated JSON object, a lone UTF-16 BOM, random-looking bytes.
    """
    parts: list[bytes] = [
        b"\xff\xfe\x00\x01 invalid utf-8 start",
        b"\x00" * 64,
        b"x",
        b"",
        b"A" * 200_000,
        b'{"evt":"auth","msg":"user=trunc',
        b"\xff\xfe",
        bytes((i * 7 + 13) % 256 for i in range(512)),
        b"<134>Sep 26 14:05:11 fw01 app[233]: \xc3\x28 bad continuation byte",
        b"CEF:0|Acme|NGFW|9.1|100|",  # header cut mid-way
    ]
    return b"\n".join(parts) + b"\n"


TEXT_FILES = {
    "linux_sshd.log": linux_sshd,
    "acme_ngfw_cef.log": acme_ngfw_cef,
    "authsrv_t1_ok.log": authsrv_t1_ok,
    "authsrv_t2_session.log": authsrv_t2_session,
    "authsrv_t3_failed.log": authsrv_t3_failed,
    "ot_historian.log": ot_historian,
}

# Which vendor each corpus file belongs to, used by demo/tools/fake_raw.py.
VENDOR_BY_FILE = {
    "linux_sshd.log": "linux",
    "acme_ngfw_cef.log": "acme_ngfw",
    "authsrv_t1_ok.log": "custom",
    "authsrv_t2_session.log": "custom",
    "authsrv_t3_failed.log": "custom",
    "ot_historian.log": "unregistered",
    "garbage.bin": "unregistered",
}


def main() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    for name, builder in TEXT_FILES.items():
        text = builder()
        (CORPUS / name).write_text(text, encoding="utf-8")
        print(f"{name:<26} {len(text.splitlines()):>4} lines")
    (CORPUS / "garbage.bin").write_bytes(garbage_bin())
    print(f"{'garbage.bin':<26} {len(garbage_bin()):>4} bytes")


if __name__ == "__main__":
    main()
