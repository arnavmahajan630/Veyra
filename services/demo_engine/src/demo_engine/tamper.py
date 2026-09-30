"""Tamper bridge: exposes B5's tamper lab over IF-API-DEMO (B7).

A thin bridge on purpose. ``tools/tamper.py`` owns what each mode does, backs every changed
file up before touching it, and re-runs B4's verification afterwards; duplicating the
dispatch here would mean two definitions of "an insider rewrite" that could drift apart —
and would skip the backup on any path this file forgot.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[4]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))


class TamperUnavailable(RuntimeError):
    """The tamper lab could not be opened — no vault, or the tools are not on the path."""


class TamperBridge:
    def __init__(self) -> None:
        self._lab: Any = None

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def _lib() -> Any:
        try:
            from tools import tamper as tamper_lib
        except ImportError as exc:  # pragma: no cover - only when tools/ is missing
            raise TamperUnavailable(f"the tamper lab is not importable: {exc}") from exc
        return tamper_lib

    def lab(self) -> Any:
        if self._lab is None:
            self._lab = self._lib().open_lab()
        return self._lab

    def modes(self) -> tuple[str, ...]:
        return tuple(self._lib().MODES)

    # ------------------------------------------------------------------ actions
    def default_event(self) -> str:
        """The event a tamper targets when the caller names none.

        The newest sealed segment's first record: by the time Beat 5 runs, that is the
        replayed T3 event the presenter is looking at.
        """
        lab = self.lab()
        segments = lab.locator.segment_paths()
        if not segments:
            raise TamperUnavailable("no sealed segment in the vault yet; nothing to tamper")
        for record in lab.locator.reader(segments[0]).records():
            return str(record.event_uid)
        raise TamperUnavailable(f"segment {segments[0].name} holds no records")

    def tamper(self, mode: str, event_uid: str | None = None) -> dict[str, Any]:
        """Apply one mode and return B5's report, including B4's verdict."""
        lib = self._lib()
        if mode not in lib.MODES:
            raise ValueError(f"unknown tamper mode {mode!r}; choose from {', '.join(lib.MODES)}")
        uid = event_uid or self.default_event()
        result = lib.tamper(self.lab(), mode, uid)
        # `target` is what the demo panel and the verify panel show.
        result["target"] = f"{result['segment_id']} ({uid})"
        return dict(result)

    def untamper(self, event_uid: str | None = None) -> dict[str, Any]:
        """Restore from the pristine copies. Without a uid, undoes everything."""
        result = self._lib().untamper(self.lab(), event_uid=event_uid)
        return {"ok": True, **result}

    def active(self) -> list[dict[str, Any]]:
        """What is tampered right now, so the panel can show it and offer Untamper."""
        try:
            return list(self._lib().active(self.lab()))
        except TamperUnavailable:
            return []
