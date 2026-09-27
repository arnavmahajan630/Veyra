"""argon2 password hashing and opaque session tokens (only their digest is stored)."""

from __future__ import annotations

import hashlib

from control_api.security import hash_password, new_session_token, token_digest, verify_password


def test_password_round_trip() -> None:
    hashed = hash_password("veyra-demo")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "veyra-demo")
    assert not verify_password(hashed, "wrong")
    assert not verify_password("not-a-hash", "veyra-demo")


def test_session_tokens_are_random_and_digested_with_sha256() -> None:
    first, second = new_session_token(), new_session_token()
    assert first != second and len(first) >= 43
    assert token_digest(first) == hashlib.sha256(first.encode()).hexdigest()
