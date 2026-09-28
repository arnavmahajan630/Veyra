"""The API-key pepper, shared with control-api through a file.

C1 generates `data/keys/api_pepper` at first boot and hashes secrets as
``sha256(pepper + secret)`` (`control_api.keys.secret_digest`). The gateway must hash identically,
so it reads the same file rather than deriving anything of its own.

Two failure modes are worth distinguishing, because they look the same from the outside and need
opposite fixes: the file does not exist yet (control-api has not booted — wait), and the file exists
but cannot be read (it is mode 0400 owned by another uid — a deployment fix).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


class PepperUnavailable(RuntimeError):
    """Raised when the pepper cannot be read, with a message an operator can act on."""


def load_pepper(keys_dir: Path, *, timeout_s: float = 60.0, poll_s: float = 1.0) -> bytes:
    """Read the pepper, waiting up to ``timeout_s`` for control-api to create it."""
    path = keys_dir / "api_pepper"
    deadline = time.monotonic() + timeout_s
    announced = False
    while True:
        try:
            pepper = path.read_bytes().strip()
        except FileNotFoundError:
            if not announced:
                log.info("waiting for the api pepper", extra={"path": str(path)})
                announced = True
        except PermissionError as exc:
            # Not worth retrying: the file is there and the uid is wrong. C1 writes it 0400 as the
            # invoking user, so the gateway container must run as that same uid (compose `user:`).
            raise PepperUnavailable(
                f"cannot read {path}: {exc}. control-api writes it mode 0400 as the invoking "
                "user, so ingest-gateway must run as the same uid (compose `user:`)."
            ) from exc
        else:
            if pepper:
                return pepper
            log.warning("api pepper is empty, waiting", extra={"path": str(path)})
        if time.monotonic() >= deadline:
            raise PepperUnavailable(
                f"{path} did not appear within {timeout_s:.0f}s — is control-api running? "
                "(`make up SERVICES=c1`, or `tools/mock_control_publish.py --api-key`)"
            )
        time.sleep(poll_s)
