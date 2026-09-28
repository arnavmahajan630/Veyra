"""Loading the shared pepper, including the two failures that look identical from outside."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from ingest_gateway.pepper import PepperUnavailable, load_pepper


def test_reads_the_pepper_control_api_wrote(tmp_path: Path) -> None:
    (tmp_path / "api_pepper").write_text("deadbeef\n")
    assert load_pepper(tmp_path) == b"deadbeef"


def test_waits_for_the_file_then_reads_it(tmp_path: Path) -> None:
    """control-api may boot after the gateway; that is not an error, it is a wait."""

    def write_later() -> None:
        (tmp_path / "api_pepper").write_text("cafebabe")

    timer = threading.Timer(0.3, write_later)
    timer.start()
    try:
        assert load_pepper(tmp_path, timeout_s=10, poll_s=0.1) == b"cafebabe"
    finally:
        timer.cancel()


def test_gives_up_with_an_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(PepperUnavailable, match="is control-api running"):
        load_pepper(tmp_path, timeout_s=0.2, poll_s=0.05)


def test_an_unreadable_pepper_fails_immediately_and_says_why(tmp_path: Path) -> None:
    """Mode 0400 owned by another uid is a deployment fix, not something to wait for."""
    path = tmp_path / "api_pepper"
    path.write_text("secret")
    path.chmod(0o000)
    try:
        with pytest.raises(PepperUnavailable, match="same uid"):
            load_pepper(tmp_path, timeout_s=5)
    finally:
        path.chmod(0o600)


def test_an_empty_pepper_is_not_accepted(tmp_path: Path) -> None:
    (tmp_path / "api_pepper").write_text("   ")
    with pytest.raises(PepperUnavailable):
        load_pepper(tmp_path, timeout_s=0.2, poll_s=0.05)
