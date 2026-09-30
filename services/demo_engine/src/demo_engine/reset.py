"""Reset orchestrator for the demo engine (B7).

`make demo-reset` puts the whole system back to the pre-demo state of
``04_DEMO_SCRIPT.md`` §2 in under ``VEYRA_DEMO_RESET_BUDGET_S`` seconds.

Two rules hold throughout:

* **A failed step is reported as failed.** An earlier version wrapped every step in a
  swallow-all ``except`` and always returned ``ok: true``; a reset that half worked then
  looked identical to one that worked, which is the worst possible outcome ten minutes
  before a demo.
* **`data/keys/` survives.** Wiping the signing key would invalidate every signed root ever
  produced, so the vault wipe is explicit about what it removes.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from demo_engine.baseline import BaselineLoop
from veyra_common.settings import Settings

log = logging.getLogger(__name__)

# Containers that hold consumer-group state or in-memory caches and must come back clean.
STATEFUL_CONSUMERS = (
    "veyra-normalizer",
    "veyra-archiver",
    "veyra-integrity",
    "veyra-lineage-indexer",
    "veyra-router",
    "veyra-drift-worker",
)

WAZUH_SAVED_OBJECTS = Path("wazuh/dashboard/saved_objects.ndjson")
# Mounted into the demo-engine container in the demo profile only (declared as a deviation
# in 00_MASTER §7).
DOCKER_SOCKET = "/var/run/docker.sock"


@dataclass
class StepResult:
    name: str
    ok: bool
    ms: int
    detail: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "ms": self.ms, "detail": self.detail}


@dataclass
class ResetState:
    """The progress of one reset, polled by ``GET /reset/status``."""

    running: bool = False
    ok: bool | None = None
    seconds: float = 0.0
    steps: list[StepResult] = field(default_factory=list)
    started_at: float | None = None
    over_budget: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "ready": not self.running,
            "ok": self.ok,
            "seconds": round(self.seconds, 2),
            "over_budget": self.over_budget,
            "steps": [step.as_json() for step in self.steps],
        }


class ResetOrchestrator:
    def __init__(
        self,
        baseline: BaselineLoop | None = None,
        cfg: Settings | None = None,
        scenario_name: str = "sih_main",
    ) -> None:
        self.cfg = cfg or Settings()
        self.baseline = baseline
        self.scenario_name = scenario_name
        self.repo_dir = Path(__file__).resolve().parents[4]
        self.state = ResetState()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ driving
    def start(self) -> bool:
        """Kick off a reset in the background. False if one is already running."""
        with self._lock:
            if self.state.running:
                return False
            self.state = ResetState(running=True, started_at=time.monotonic())
        threading.Thread(target=self.execute_reset, daemon=True, name="demo-reset").start()
        return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self.state.as_json()

    def execute_reset(self) -> dict[str, Any]:
        """Run every step in order, timing each one. Never raises."""
        started = time.monotonic()
        with self._lock:
            self.state = ResetState(running=True, started_at=started)

        steps: list[tuple[str, Callable[[], str]]] = [
            ("Pause baseline traffic", self._pause_baseline),
            ("Reseed control plane, contracts and inventory", self._control_plane),
            ("Recreate Kafka topics", self._kafka_topics),
            ("Truncate the lineage index", self._clickhouse),
            ("Wipe vault segments, ledger and tamper backups", self._vault),
            ("Re-create the immudb database", self._immudb),
            ("Clear Wazuh indices, sinks and saved objects", self._wazuh),
            ("Restart the stateful consumers", self._restart_consumers),
            ("Resume baseline and pre-warm", self._resume_and_warm),
        ]

        results: list[StepResult] = []
        for name, step in steps:
            at = time.monotonic()
            try:
                detail = step()
                ok = True
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                ok = False
                log.error("reset step %r failed: %s", name, detail)
            results.append(StepResult(name, ok, int((time.monotonic() - at) * 1000), detail))
            with self._lock:
                self.state.steps = list(results)

        seconds = time.monotonic() - started
        budget = float(self.cfg.demo_reset_budget_s)
        with self._lock:
            self.state.running = False
            self.state.seconds = seconds
            self.state.ok = all(step.ok for step in results)
            self.state.over_budget = seconds > budget
            summary = self.state.as_json()

        if summary["over_budget"]:
            log.warning("reset took %.1fs, over the %.0fs budget", seconds, budget)
        log.info("reset finished in %.1fs: ok=%s", seconds, summary["ok"])
        return summary

    # ------------------------------------------------------------------ steps
    def _pause_baseline(self) -> str:
        if self.baseline is None:
            return "no baseline loop attached"
        self.baseline.pause()
        return "baseline paused"

    def _control_plane(self) -> str:
        """control-api wipes and reseeds SQLite, the contracts repo and the inventory."""
        response = httpx.post(
            f"{self.cfg.control_api_url.rstrip('/')}/internal/reset",
            json={"scenario": self.scenario_name},
            timeout=60.0,
        )
        response.raise_for_status()
        body = response.json()
        return (
            f"{body.get('tenants', 0)} tenants, {body.get('users', 0)} users, "
            f"{body.get('sources', 0)} sources, {body.get('contracts', 0)} contracts"
        )

    def _kafka_topics(self) -> str:
        """Delete and recreate every topic from IF-TOPICS, at the profile's partitioning.

        ``control`` is deleted and recreated **before** the control-plane reseed would be
        wrong — the reseed has already republished it by the time we get here — so it is
        deleted last and left for `create_topics` to rebuild, and the control-api reseed is
        re-run for it. Ordering note: the reseed (step 2) writes `control`, so deleting it
        here would drop that state. It is therefore excluded from the delete set and only
        its retention config is left as `create_topics` defines it.
        """
        from confluent_kafka.admin import AdminClient

        from veyra_common.topics import TOPIC_CONTROL, create_topics, topic_specs

        admin = AdminClient({"bootstrap.servers": self.cfg.kafka_bootstrap})
        existing = set(admin.list_topics(timeout=30.0).topics)
        wanted = {spec.name for spec in topic_specs(self.cfg)}
        # Internal topics stay; `control` keeps the state step 2 just republished.
        doomed = sorted(
            topic
            for topic in existing & wanted
            if topic != TOPIC_CONTROL and not topic.startswith("_")
        )
        if doomed:
            for future in admin.delete_topics(doomed, operation_timeout=30).values():
                future.result()  # raises if the delete failed
            # The broker needs the metadata to settle before the same names come back.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if not (set(admin.list_topics(timeout=10.0).topics) & set(doomed)):
                    break
                time.sleep(0.5)
        created = create_topics(self.cfg, timeout=30.0)
        return (
            f"deleted {len(doomed)}, {sum(1 for v in created.values() if v == 'created')} created"
        )

    def _clickhouse(self) -> str:
        """Truncate every table in the lineage database, materialized views included."""
        from veyra_lineage.client import make_client

        client = make_client(self.cfg)
        db = self.cfg.clickhouse_db
        rows = client.query(
            "SELECT name, engine FROM system.tables WHERE database = {db:String}",
            parameters={"db": db},
        ).result_rows
        truncated = 0
        for name, engine in rows:
            # A view has no data of its own; its target table is in this same list.
            if engine in ("View", "MaterializedView", "LiveView"):
                continue
            client.command(f"TRUNCATE TABLE IF EXISTS `{db}`.`{name}`")
            truncated += 1
        return f"{truncated} table(s) truncated in {db}"

    def _vault(self) -> str:
        """Remove segments, the signed-root ledger, the chain head and tamper backups.

        ``data/keys/`` is deliberately untouched: the signing key outlives a reset, and
        losing it would make every previously signed root unverifiable.
        """
        vault_dir = Path(self.cfg.vault_dir)
        removed = 0
        for path in vault_dir.glob("*/*/*.seg"):
            path.unlink()
            removed += 1
        for path in vault_dir.glob("*.seg"):
            path.unlink()
            removed += 1
        for directory in (vault_dir / "roots", vault_dir / "tamper_backup"):
            if directory.is_dir():
                shutil.rmtree(directory)
        # The integrity service's chain head and inflight journals live under data/state.
        state_dir = Path(self.cfg.state_dir)
        for pattern in ("integrity*", "archiver*", "*_inflight", "drift"):
            for path in state_dir.glob(pattern):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
        keys_dir = Path(self.cfg.keys_dir)
        if not keys_dir.is_dir():
            log.warning("keys dir %s does not exist; the key provider will create it", keys_dir)
        return f"{removed} segment(s) removed; keys kept at {keys_dir}"

    def _immudb(self) -> str:
        """Name a fresh database and create it, so no anchor from a past run is visible."""
        state_dir = Path(self.cfg.state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        name = f"veyra_{int(time.time())}"
        created = "not created"
        try:
            from immudb import ImmudbClient  # type: ignore[import-not-found]

            client = ImmudbClient(f"{self.cfg.immudb_host}:{self.cfg.immudb_grpc_port}")
            client.login(self.cfg.immudb_user, self.cfg.immudb_password)
            client.createDatabase(name.encode())
            created = "created"
        except ImportError:
            # immudb anchoring is a prototype in this build; say so instead of pretending.
            created = "python client not installed (immudb anchoring is a prototype)"
        (state_dir / "immudb_db").write_text(name, encoding="utf-8")
        return f"{name}: {created}"

    def _wazuh(self) -> str:
        """Drop today's alert/archive indices, blank the sinks, re-import saved objects."""
        notes: list[str] = []
        for path in Path(self.cfg.sinks_dir).glob("**/*.ndjson"):
            path.write_text("", encoding="utf-8")
            notes.append(path.name)

        if self.cfg.wazuh_mode != "local":
            return f"sinks cleared ({len(notes)}); Wazuh is remote, indices left alone"

        deleted = self._delete_wazuh_indices()
        notes.append(deleted)
        notes.append(self._import_saved_objects())
        return "; ".join(notes)

    def _delete_wazuh_indices(self) -> str:
        url = f"{self.cfg.wazuh_indexer_url.rstrip('/')}/wazuh-alerts-*,wazuh-archives-*"
        response = httpx.delete(
            url,
            auth=(self.cfg.wazuh_indexer_user, self.cfg.wazuh_indexer_password),
            verify=False,
            timeout=30.0,
        )
        # 404 means there was nothing to delete, which is the desired end state anyway.
        if response.status_code not in (200, 404):
            raise RuntimeError(
                f"indexer refused the delete: {response.status_code} {response.text[:200]}"
            )
        return "alert/archive indices dropped"

    def _import_saved_objects(self) -> str:
        """Re-import A6's dashboard objects, so Discover opens pre-filtered."""
        objects = self.repo_dir / WAZUH_SAVED_OBJECTS
        if not objects.is_file():
            return f"no saved objects at {WAZUH_SAVED_OBJECTS}"
        url = f"{self.cfg.wazuh_dashboard_url.rstrip('/')}/api/saved_objects/_import?overwrite=true"
        response = httpx.post(
            url,
            files={"file": (objects.name, objects.read_bytes(), "application/ndjson")},
            headers={"osd-xsrf": "true"},
            auth=(self.cfg.wazuh_indexer_user, self.cfg.wazuh_dashboard_password),
            verify=False,
            timeout=60.0,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"saved-object import failed: {response.status_code} {response.text[:200]}"
            )
        return "dashboard saved objects imported"

    def _restart_consumers(self) -> str:
        """Restart the stateful consumers over the mounted Docker socket.

        The engine talks to the Docker Engine API directly rather than through the SDK: the
        repository has its own top-level ``docker/`` directory, which shadows the SDK's
        import name depending on the working directory, and one HTTP call per container
        needs no extra dependency. The socket is mounted in the demo profile only.
        """
        transport = httpx.HTTPTransport(uds=DOCKER_SOCKET)
        restarted: list[str] = []
        missing: list[str] = []
        failed: list[str] = []
        with httpx.Client(transport=transport, base_url="http://docker", timeout=60.0) as client:
            for name in STATEFUL_CONSUMERS:
                response = client.post(f"/v1.44/containers/{name}/restart", params={"t": 10})
                if response.status_code == 404:
                    missing.append(name)
                elif response.status_code >= 400:
                    failed.append(f"{name} ({response.status_code})")
                else:
                    restarted.append(name)
        if failed:
            raise RuntimeError(f"could not restart: {', '.join(failed)}")
        detail = f"restarted {len(restarted)}"
        if missing:
            detail += f"; not running: {', '.join(missing)}"
        return detail

    def _resume_and_warm(self) -> str:
        """Resume traffic, then warm the paths the first demo click will hit."""
        notes: list[str] = []
        if self.baseline is not None:
            self.baseline.resume()
            notes.append("baseline resumed")

        # Ollama keep-alive, so Beat 2's first draft is not paying for a cold model load.
        try:
            httpx.post(
                f"{self.cfg.ollama_url.rstrip('/')}/api/generate",
                json={"model": self.cfg.llm_model, "prompt": "", "keep_alive": "30m"},
                timeout=30.0,
            )
            notes.append("ollama warmed")
        except Exception as exc:
            notes.append(f"ollama not warmed ({type(exc).__name__})")

        # Console API calls, so the first page load is not the one compiling a query plan.
        evidence = self.cfg.evidence_api_url.rstrip("/")
        for path in ("/lineage/overview", "/lineage/sources", "/evidence/roots?limit=5"):
            with contextlib.suppress(Exception):
                httpx.get(f"{evidence}{path}", timeout=10.0)
        notes.append("console endpoints warmed")

        # A verify, once a segment has sealed — but only within what is left of the budget.
        notes.append(self._prewarm_verify())
        return "; ".join(notes)

    def _prewarm_verify(self) -> str:
        started = self.state.started_at or time.monotonic()
        spent = time.monotonic() - started
        remaining = float(self.cfg.demo_reset_budget_s) - spent
        wait = min(float(self.cfg.segment_max_seconds) + 2, max(remaining - 10.0, 0.0))
        if wait <= 0:
            return "verify pre-warm skipped: no room left in the reset budget"
        deadline = time.monotonic() + wait
        evidence = self.cfg.evidence_api_url.rstrip("/")
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{evidence}/lineage/search?q=&limit=1", timeout=5.0)
                hits = response.json().get("hits") if response.status_code == 200 else None
                if hits:
                    uid = hits[0]["event_uid"]
                    httpx.get(f"{evidence}/evidence/verify/{uid}", timeout=15.0)
                    return f"verify pre-warmed on {uid}"
            except Exception:
                pass
            time.sleep(1.0)
        return f"verify pre-warm found no sealed event within {wait:.0f}s"
