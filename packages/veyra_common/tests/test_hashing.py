"""IF-TEMPLATE-SIG is frozen: these are the vectors from docs/plan/02_CONTRACTS.md.

If one of these fails, the change is breaking (02_CONTRACTS §0) and needs all three
owners' agreement plus a major contract bump — not a fix here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from veyra_common.hashing import sha256_hex, template_sig, template_sig_parts

SPEC_VECTORS = (
    Path(__file__).resolve().parents[3] / "docs" / "plan" / "reference" / "spec_vectors.py"
)

FROZEN = [
    (
        "authsrv",
        "user=neel.k FAILED login from 45.12.3.9 via 10.2.3.4 attempts:3",
        "user=<V> FAILED login from <IP> via <IP> attempts:<V>",
        "t_3c85a1bfbf81",
    ),
    (
        "authsrv",
        "user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1",
        "user=<V> FAILED login from <IP> via <IP> attempts:<V>",
        "t_3c85a1bfbf81",
    ),
    (
        "unregistered",
        "Sep 26 14:05:11 conn 88213 closed by 10.0.0.5",
        "Sep <NUM> <TS> conn <NUM> closed by <IP>",
        "t_940f551bf008",
    ),
]


@pytest.mark.parametrize(("scope", "text", "masked", "sig"), FROZEN)
def test_frozen_vectors(scope: str, text: str, masked: str, sig: str) -> None:
    got_masked, got_sig = template_sig_parts(scope, text)
    assert got_masked == masked
    assert got_sig == sig
    assert template_sig(scope, text) == sig


def test_same_shape_different_values_collapse() -> None:
    """The whole point of a signature: values vary, the shape does not."""
    a = template_sig("authsrv", "user=neel.k FAILED login from 45.12.3.9 via 10.2.3.4 attempts:3")
    b = template_sig("authsrv", "user=x.y FAILED login from 8.8.8.8 via 1.1.1.1 attempts:99")
    assert a == b


def test_scope_separates() -> None:
    text = "user=neel.k FAILED login from 45.12.3.9 via 10.2.3.4 attempts:3"
    assert template_sig("authsrv", text) != template_sig("linux_sshd", text)


def test_sig_is_stable_under_whitespace_runs() -> None:
    assert template_sig("s", "a  b\tc") == template_sig("s", "a b c")


def test_sha256_hex_matches_reference_raws() -> None:
    """The three raw lines in spec_vectors.py, hashed here."""
    raws = [
        b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=neel.k FAILED login '
        b'from 45.12.3.9 via 10.2.3.4 attempts:3"} | trace=\n  at com.x.Auth.login(Auth.java:88)',
        b"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user admin "
        b"from 45.12.3.9 port 52144 ssh2",
        b"CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22 act=deny",
    ]
    expected = [
        "42d88deeb474802a9dce2c9971f2613aa1a95cec8b0102011fc88a8c2894ab57",
        "d0d2cdd3",  # prefix only in the contract table; asserted by the runner below
        "9703bf6c",
    ]
    assert sha256_hex(raws[0]) == expected[0]
    assert sha256_hex(raws[1]).startswith(expected[1])
    assert sha256_hex(raws[2]).startswith(expected[2])


@pytest.mark.skipif(not SPEC_VECTORS.exists(), reason="plan folder not present")
def test_agrees_with_reference_runner() -> None:
    """Run the plan's own reference script and compare, so drift is impossible."""
    out = subprocess.run(
        [sys.executable, str(SPEC_VECTORS)], capture_output=True, text=True, check=True
    )
    ref = json.loads(out.stdout)
    for item in ref["template_sig"]:
        masked, sig = template_sig_parts(item["scope"], item["text"])
        assert masked == item["masked"]
        assert sig == item["sig"]
    for item in ref["raw_sha256"]:
        assert sha256_hex(item["raw_utf8"].encode()) == item["sha256"]
