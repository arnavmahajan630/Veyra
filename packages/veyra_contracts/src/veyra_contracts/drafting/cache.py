"""Recorded drafts (C4 "Cache"): ``<dir>/<template_sig>.json`` per shape.

An entry keeps the raw model response with the model and the prompt hash that produced
it, so a stale entry (another model, a changed prompt) is still served but flagged.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CacheEntry:
    response: dict[str, Any]
    model: str
    prompt_sha: str
    created_at: str


class DraftCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, sig: str) -> Path:
        return self.directory / f"{sig}.json"

    def get(self, sig: str) -> CacheEntry | None:
        path = self._path(sig)
        if not path.is_file():
            return None
        return CacheEntry(**json.loads(path.read_text(encoding="utf-8")))

    def put(
        self, sig: str, response: dict[str, Any], *, model: str, prompt_sha: str, created_at: str
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = CacheEntry(response, model, prompt_sha, created_at)
        tmp = self._path(sig).with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(entry), indent=2), encoding="utf-8")
        os.replace(tmp, self._path(sig))
