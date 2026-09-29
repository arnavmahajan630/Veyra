"""IF-KEYPROVIDER — where segment keys come from.

Prototype scope (B2): :class:`LocalKeyProvider` only, and only the data-key half of the
interface. A master KEK lives in ``data/keys/kek.bin``; every segment gets a fresh
AES-256 data key (DEK) wrapped under it with AES-GCM, so the vault is encrypted per
segment and one leaked DEK exposes one segment.

``sign`` / ``public_key_pem`` (Ed25519 window roots) land in B3, and the OpenBao provider
with them; the Protocol below names them so the shape does not change later.

    python -m veyra_evidence.keys init        # create data/keys/kek.bin if absent
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from pathlib import Path
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from veyra_common.settings import Settings, settings

log = logging.getLogger(__name__)

KEK_FILE = "kek.bin"
KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # GCM standard
LOCAL_KEY_ID = "veyra-local-kek-1"
# The KEK file is a secret: owner-read-only, like an ssh private key.
SECRET_MODE = 0o400


class KeyError_(Exception):
    """A key could not be loaded, wrapped or unwrapped."""


@runtime_checkable
class KeyProvider(Protocol):
    """IF-KEYPROVIDER. B2 implements the first two; B3 adds the signing half."""

    def new_data_key(self) -> tuple[bytes, str, bytes]:
        """``(plaintext_dek, key_id, wrapped_dek)`` for one new segment."""
        ...

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes:
        """Recover a segment's DEK, for reading it back."""
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
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write through a temp file with the final mode already set, so the secret is
        # never briefly world-readable, and rename atomically.
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SECRET_MODE)
        try:
            os.write(fd, secrets.token_bytes(KEY_BYTES))
            os.fsync(fd)
        finally:
            os.close(fd)
        tmp.replace(path)
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


def main() -> None:
    """``python -m veyra_evidence.keys init``."""
    import sys

    from veyra_common.logging import setup_logging

    setup_logging("veyra_evidence.keys", settings.log_level)
    command = sys.argv[1] if len(sys.argv) > 1 else "init"
    if command != "init":
        raise SystemExit(f"usage: python -m veyra_evidence.keys init (got {command!r})")
    provider = get_key_provider()
    path = provider.ensure_kek()
    print(f"KEK ready: {path} (mode {oct(path.stat().st_mode & 0o777)})")


if __name__ == "__main__":
    main()
