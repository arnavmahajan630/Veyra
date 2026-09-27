"""The vocab, enrich and routes documents control-api publishes (from ``seed/``)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import yaml

from veyra_common.models import EnrichMessage, RoutesMessage, VocabMessage

SEED_DIR = Path(__file__).resolve().parents[2] / "seed"


@dataclass(frozen=True)
class SeedDocs:
    vocab: list[VocabMessage]
    enrich: list[EnrichMessage]
    routes: RoutesMessage


def load_seed_docs(seed_dir: Path = SEED_DIR) -> SeedDocs:
    vocab = [
        VocabMessage.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((seed_dir / "vocab").glob("*.json"))
    ]
    enrich = [_csv_table(path) for path in sorted((seed_dir / "enrich").glob("*.csv"))]
    routes = RoutesMessage.model_validate(
        yaml.safe_load((seed_dir / "routes.yaml").read_text(encoding="utf-8"))
    )
    return SeedDocs(vocab=vocab, enrich=enrich, routes=routes)


def _csv_table(path: Path) -> EnrichMessage:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = [dict(row) for row in csv.DictReader(fh)]
    return EnrichMessage(name=path.stem, version=1, rows=rows)
