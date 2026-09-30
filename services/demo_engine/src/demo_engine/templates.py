"""Bridge to ``demo/generators.py``, which lives outside the Python packages.

The generators sit in ``demo/`` next to the corpus they complement, and that directory is
copied into the image but is not an installed package. This keeps the ``sys.path`` handling
in one place instead of spreading it through the senders.
"""

from __future__ import annotations

import datetime
import importlib.util
import random
from functools import lru_cache
from pathlib import Path
from types import ModuleType

GENERATORS_PATH = Path(__file__).resolve().parents[4] / "demo" / "generators.py"


@lru_cache(maxsize=1)
def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("veyra_demo_generators", GENERATORS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the demo generators from {GENERATORS_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def known_templates() -> frozenset[str]:
    """Template names a scenario may reference; the validator checks against this."""
    return frozenset(_module().GENERATORS)


def generate_line(
    name: str, now: datetime.datetime, vary: dict[str, str], rng: random.Random
) -> str:
    return str(_module().generate(name, now, vary, rng))
