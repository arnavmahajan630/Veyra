"""IF-KEYPROVIDER — where segment keys come from.

:class:`LocalKeyProvider` holds both halves of the interface:

* **data keys (B2).** A master KEK in ``data/keys/kek.bin``; every segment gets a fresh
  AES-256 DEK wrapped under it with AES-GCM, so one leaked DEK exposes one segment.
* **signing (B3).** An Ed25519 private key in ``data/keys/root_ed25519.pem`` signs window
  roots (IF-SIGNED-ROOT). Verification needs only the public key, so an auditor can check
  the ledger without any secret.

Both files are created on first use with mode 0400 and are never overwritten — every
sealed segment and every signed root already depends on them. The OpenBao provider is
still a later phase.

    python -m veyra_evidence.keys init        # create both keys if absent
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from pathlib import Path
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from veyra_common.settings import Settings, settings

log = logging.getLogger(__name__)

KEK_FILE = "kek.bin"
SIGNING_KEY_FILE = "root_ed25519.pem"
KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # GCM standard
LOCAL_KEY_ID = "veyra-local-kek-1"
LOCAL_SIGNING_KEY_ID = "veyra-root-ed25519-1"  # IF-SIGNED-ROOT key_id
# The KEK file is a secret: owner-read-only, like an ssh private key.
SECRET_MODE = 0o400


class KeyError_(Exception):
    """A key could not be loaded, wrapped, unwrapped or used to sign."""


@runtime_checkable
class KeyProvider(Protocol):
    """IF-KEYPROVIDER. B2 implements the first two; B3 adds the signing half."""

    def new_data_key(self) -> tuple[bytes, str, bytes]:
        """``(plaintext_dek, key_id, wrapped_dek)`` for one new segment."""
        ...

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes:
        """Recover a segment's DEK, for reading it back."""
        ...

    def sign(self, key_id: str, payload: bytes) -> bytes:
        """Ed25519 signature over ``payload`` (IF-SIGNED-ROOT)."""
        ...

    def public_key_pem(self, key_id: str) -> str:
        """The verifying key, which is safe to publish."""
        ...


class LocalKeyProvider:
    """KEK in ``data/keys/kek.bin``, DEKs wrapped under it with AES-GCM.

    The wrapped form is ``nonce(12) || ciphertext || tag``, with the key id as AAD so a
    blob wrapped under one key id cannot be replayed under another.
    """

    def __init__(self, keys_dir: Path | None = None, cfg: Settings | None = None) -> None:
        s = cfg or settings
        self.keys_dir = Path(keys_dir) if keys_dir is not None else s.keys_dir
        self.key_id = LOCAL_KEY_ID
        self.signing_key_id = LOCAL_SIGNING_KEY_ID

    # ---------------------------------------------------------------- KEK
    @property
    def kek_path(self) -> Path:
        return self.keys_dir / KEK_FILE

    def ensure_kek(self) -> Path:
        """Create the KEK on first boot; never overwrite an existing one."""
        path = self.kek_path
        if path.exists():
            if path.stat().st_size != KEY_BYTES:
                raise KeyError_(f"{path} is not a {KEY_BYTES}-byte key")
            return path
        _write_secret(path, secrets.token_bytes(KEY_BYTES))
        log.info("created a new KEK", extra={"path": str(path)})
        return path

    def _kek(self) -> bytes:
        try:
            return self.ensure_kek().read_bytes()
        except OSError as exc:
            raise KeyError_(f"cannot read the KEK: {exc}") from exc

    # ---------------------------------------------------------------- IF-KEYPROVIDER
    def new_data_key(self) -> tuple[bytes, str, bytes]:
        dek = secrets.token_bytes(KEY_BYTES)
        nonce = secrets.token_bytes(NONCE_BYTES)
        wrapped = nonce + AESGCM(self._kek()).encrypt(nonce, dek, self.key_id.encode())
        return dek, self.key_id, wrapped

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes:
        if key_id != self.key_id:
            raise KeyError_(f"unknown key id {key_id!r} (this provider holds {self.key_id!r})")
        if len(wrapped) <= NONCE_BYTES:
            raise KeyError_("wrapped DEK is truncated")
        nonce, blob = wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:]
        try:
            return AESGCM(self._kek()).decrypt(nonce, blob, key_id.encode())
        except InvalidTag as exc:
            raise KeyError_("the wrapped DEK failed its integrity check") from exc

    # ---------------------------------------------------------------- signing (B3)
    @property
    def signing_key_path(self) -> Path:
        return self.keys_dir / SIGNING_KEY_FILE

    def ensure_signing_key(self) -> Path:
        """Create the Ed25519 root key on first boot; never overwrite an existing one.

        Replacing it would orphan every root already in the ledger, so an existing file
        is always kept.
        """
        path = self.signing_key_path
        if path.exists():
            return path
        pem = Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _write_secret(path, pem)
        log.info("created a new Ed25519 root key", extra={"path": str(path)})
        return path

    def _signing_key(self) -> Ed25519PrivateKey:
        try:
            pem = self.ensure_signing_key().read_bytes()
        except OSError as exc:
            raise KeyError_(f"cannot read the signing key: {exc}") from exc
        try:
            key = serialization.load_pem_private_key(pem, password=None)
        except Exception as exc:
            raise KeyError_(f"the signing key is not a valid PEM private key: {exc}") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise KeyError_(f"expected an Ed25519 key, found {type(key).__name__}")
        return key

    def _check_signing_key_id(self, key_id: str) -> None:
        if key_id != self.signing_key_id:
            raise KeyError_(
                f"unknown signing key id {key_id!r} (this provider holds {self.signing_key_id!r})"
            )

    def sign(self, key_id: str, payload: bytes) -> bytes:
        """Ed25519 signature over the canonical payload bytes (IF-SIGNED-ROOT)."""
        self._check_signing_key_id(key_id)
        return self._signing_key().sign(payload)

    def public_key_pem(self, key_id: str) -> str:
        """The verifying key in PEM form — safe to publish next to the ledger."""
        self._check_signing_key_id(key_id)
        return (
            self._signing_key()
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )


def _write_secret(path: Path, data: bytes) -> None:
    """Create a 0400 file atomically; never clobber an existing secret."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SECRET_MODE)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(path)


def get_key_provider(cfg: Settings | None = None) -> LocalKeyProvider:
    """The provider named by ``VEYRA_KEY_PROVIDER``. Only ``local`` exists in B2."""
    s = cfg or settings
    if s.key_provider != "local":
        raise NotImplementedError(
            f"key provider {s.key_provider!r} is not implemented yet (B3); use 'local'"
        )
    return LocalKeyProvider(cfg=s)


def wrapped_b64(wrapped: bytes) -> str:
    return base64.b64encode(wrapped).decode()


def verify_signature(public_key_pem: str, payload: bytes, signature: bytes) -> bool:
    """Check an Ed25519 signature with only the public key (what an auditor has)."""
    try:
        key = serialization.load_pem_public_key(public_key_pem.encode())
    except Exception as exc:
        raise KeyError_(f"not a valid public key: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise KeyError_(f"expected an Ed25519 public key, got {type(key).__name__}")
    try:
        key.verify(signature, payload)
    except InvalidSignature:
        return False
    return True


def main() -> None:
    """``python -m veyra_evidence.keys init``."""
    import sys

    from veyra_common.logging import setup_logging

    setup_logging("veyra_evidence.keys", settings.log_level)
    command = sys.argv[1] if len(sys.argv) > 1 else "init"
    if command != "init":
        raise SystemExit(f"usage: python -m veyra_evidence.keys init (got {command!r})")
    provider = get_key_provider()
    for path in (provider.ensure_kek(), provider.ensure_signing_key()):
        print(f"ready: {path} (mode {oct(path.stat().st_mode & 0o777)})")
    print(provider.public_key_pem(provider.signing_key_id).strip())


if __name__ == "__main__":
    main()
