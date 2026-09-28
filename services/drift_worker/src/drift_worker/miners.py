"""One Drain3 ``TemplateMiner`` per source, persisted to ``<state>/drain3/<source>.json``.

Drain3 turns masked DLQ text into a human-readable template with ``<*>`` wildcards (for the
inbox and the drafter) and says which lines share a cluster (``related_sigs``). Two masking
rules run first, so the template reads the way C3 shows it:

- the value after ``=`` or ``:`` → ``<*>`` (``user=<*>``, ``attempts:<*>``);
- IPv4 addresses → ``<*>``.

State is saved only by ``save()`` (the worker's checkpoint), never on Drain3's own timer,
so the saved clusters always match the saved group counts.
"""

from __future__ import annotations

from pathlib import Path

from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

MASKS = (
    (r"(?<=[=:])[^\s]+", "*"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "*"),
)


def _config(sim_th: float, depth: int) -> TemplateMinerConfig:
    config = TemplateMinerConfig()
    config.profiling_enabled = False
    config.drain_sim_th = sim_th
    config.drain_depth = depth
    config.mask_prefix, config.mask_suffix = "<", ">"
    config.masking_instructions = [MaskingInstruction(regex, mask) for regex, mask in MASKS]
    config.snapshot_interval_minutes = 0
    config.snapshot_compress_state = False
    return config


class MinerPool:
    def __init__(self, state_dir: Path, *, sim_th: float, depth: int) -> None:
        self._dir = state_dir
        self._config = _config(sim_th, depth)
        self._miners: dict[str, TemplateMiner] = {}
        self._dirty: set[str] = set()

    def _path(self, source_id: str) -> Path:
        return self._dir / f"{source_id}.json"

    def _miner(self, source_id: str) -> TemplateMiner:
        miner = self._miners.get(source_id)
        if miner is None:
            self._dir.mkdir(parents=True, exist_ok=True)
            miner = TemplateMiner(FilePersistence(str(self._path(source_id))), self._config)
            self._miners[source_id] = miner
        return miner

    def add(self, source_id: str, text: str) -> int:
        """Cluster one line; returns its cluster id."""
        result = self._miner(source_id).add_log_message(text)
        self._dirty.add(source_id)
        return int(result["cluster_id"])

    def template(self, source_id: str, cluster_id: int) -> str:
        """The cluster's current template (it generalizes as more lines arrive)."""
        for cluster in self._miner(source_id).drain.clusters:
            if cluster.cluster_id == cluster_id:
                return str(cluster.get_template())
        return ""

    def save(self) -> None:
        for source_id in sorted(self._dirty):
            self._miners[source_id].save_state("checkpoint")
        self._dirty.clear()

    def reset(self) -> None:
        self._miners.clear()
        self._dirty.clear()
        for path in self._dir.glob("*.json"):
            path.unlink()
