"""Pre-demo checks (B7 + S2): ``make demo-preflight``.

Every row is measured. An earlier version returned fixed ``PASS`` strings for the Wazuh
rules, the vault and the clock, and turned two exceptions into PASS — which means the one
command whose whole job is to catch a broken laptop ten minutes before a demo could not.

Severity convention:

* ``FAIL`` — the demo cannot run as scripted.
* ``WARN`` — it runs, but a fallback will be used. A stopped Ollama is the canonical case
  (AC4): the drafter falls back to its cache, so the demo is fine and this is not a FAIL.
* ``PASS`` — measured and within limits.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from demo_engine.baseline import BaselineLoop
from demo_engine.reset import DOCKER_SOCKET, STATEFUL_CONSUMERS
from veyra_common.settings import Settings

log = logging.getLogger(__name__)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

MIN_FREE_MEMORY_GB = 3.0
MIN_FREE_DISK_GB = 10.0
MAX_CLOCK_OFFSET_S = 1.0
BASELINE_TOLERANCE = 0.30
WAZUH_RULE_RANGE = range(100100, 100131)

# Containers the demo needs up, beyond the stateful consumers.
CORE_CONTAINERS = ("veyra-kafka", "veyra-clickhouse", "veyra-control-api", "veyra-evidence-api")


@dataclass
class Check:
    check: str
    status: str
    detail: str

    def as_json(self) -> dict[str, str]:
        return {"check": self.check, "status": self.status, "detail": self.detail}


class PreflightChecker:
    def __init__(
        self,
        cfg: Settings | None = None,
        baseline: BaselineLoop | None = None,
        hotkeys_seen: Callable[[], bool] | None = None,
    ) -> None:
        self.cfg = cfg or Settings()
        self.baseline = baseline
        self.hotkeys_seen = hotkeys_seen or (lambda: False)
        self.repo_dir = Path(__file__).resolve().parents[4]

    # ------------------------------------------------------------------ driver
    def checks(self) -> list[tuple[str, Callable[[], Check]]]:
        """Every check, with the name it reports under whether it passes or blows up."""
        return [
            ("Containers", self.containers),
            ("Host memory", self.host_memory),
            ("Disk space", self.disk_space),
            ("Ollama model", self.ollama),
            ("LLM cache", self.llm_cache),
            ("Wazuh rules", self.wazuh_rules),
            ("Clock offset", self.clock_offset),
            ("Open segments", self.open_segments),
            ("Baseline EPS", self.baseline_eps),
            ("Console hotkeys", self.console_hotkeys),
        ]

    def check_all(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for name, check in self.checks():
            try:
                results.append(check().as_json())
            except Exception as exc:
                # An unexpected error is a failed check, never a silent pass — and it keeps
                # its row name, so a broken check cannot vanish from the table.
                results.append(
                    Check(
                        name, FAIL, f"the check itself failed: {type(exc).__name__}: {exc}"
                    ).as_json()
                )
        return results

    # ------------------------------------------------------------------ checks
    def containers(self) -> Check:
        """Ask the Docker socket which of the demo's containers are actually running."""
        if not Path(DOCKER_SOCKET).exists():
            return Check(
                "Containers", WARN, f"{DOCKER_SOCKET} is not mounted; container health unknown"
            )
        wanted = set(CORE_CONTAINERS) | set(STATEFUL_CONSUMERS)
        transport = httpx.HTTPTransport(uds=DOCKER_SOCKET)
        with httpx.Client(transport=transport, base_url="http://docker", timeout=10.0) as client:
            response = client.get("/v1.44/containers/json", params={"all": "true"})
            response.raise_for_status()
            containers = response.json()
        state = {
            name.lstrip("/"): entry.get("State", "unknown")
            for entry in containers
            for name in entry.get("Names", [])
        }
        unhealthy = {
            name: state.get(name, "missing")
            for name in sorted(wanted)
            if state.get(name) != "running"
        }
        if not unhealthy:
            return Check("Containers", PASS, f"all {len(wanted)} demo containers running")
        detail = ", ".join(f"{name}={status}" for name, status in unhealthy.items())
        return Check("Containers", FAIL, f"not running: {detail}")

    def host_memory(self) -> Check:
        """Free memory on the host, read from /proc/meminfo where it exists."""
        available_gb = _available_memory_gb()
        if available_gb is None:
            return Check("Host memory", WARN, "cannot read available memory on this platform")
        detail = f"{available_gb:.1f} GB available, need >= {MIN_FREE_MEMORY_GB:g} GB"
        if available_gb >= MIN_FREE_MEMORY_GB:
            return Check("Host memory", PASS, detail)
        # Under the headroom the stack still runs, but the LLM will swap.
        return Check("Host memory", FAIL if available_gb < 1.5 else WARN, detail)

    def disk_space(self) -> Check:
        free_gb = shutil.disk_usage(self.repo_dir).free / 1024**3
        detail = f"{free_gb:.1f} GB free, need >= {MIN_FREE_DISK_GB:g} GB"
        return Check("Disk space", PASS if free_gb >= MIN_FREE_DISK_GB else WARN, detail)

    def ollama(self) -> Check:
        """Is the model resident, and is it on the GPU?

        A stopped Ollama is a WARN, never a FAIL: the drafter falls back to the cache and
        the demo still runs (AC4).
        """
        try:
            response = httpx.get(f"{self.cfg.ollama_url.rstrip('/')}/api/ps", timeout=5.0)
            response.raise_for_status()
        except Exception as exc:
            return Check(
                "Ollama model",
                WARN,
                f"Ollama unreachable ({type(exc).__name__}); drafts use the cache",
            )
        models = response.json().get("models") or []
        wanted = self.cfg.llm_model
        resident = next((m for m in models if str(m.get("name", "")).startswith(wanted)), None)
        if resident is None:
            loaded = ", ".join(str(m.get("name")) for m in models) or "nothing"
            return Check(
                "Ollama model",
                WARN,
                f"{wanted} is not resident ({loaded} loaded); run `make llm-warm`",
            )
        # size_vram > 0 means at least part of the model sits on the GPU.
        on_gpu = int(resident.get("size_vram") or 0) > 0
        if on_gpu:
            return Check("Ollama model", PASS, f"{wanted} resident, on GPU")
        return Check("Ollama model", WARN, f"{wanted} resident on CPU; a live draft will be slow")

    def llm_cache(self) -> Check:
        """Does the cache hold a draft for the T3 shape the demo relies on?

        Keyed by ``template_sig`` computed with the engine's own function, so this checks the
        key the drafter will actually look up rather than "the directory is not empty".
        """
        cache_dir = Path(self.cfg.llm_cache_dir)
        if not cache_dir.is_dir():
            return Check(
                "LLM cache", WARN, f"{cache_dir} does not exist; run `make llm-cache-seed`"
            )
        try:
            sig = self._t3_template_sig()
        except Exception as exc:
            return Check("LLM cache", WARN, f"cannot compute the T3 template_sig: {exc}")
        entry = cache_dir / f"{sig}.json"
        if entry.is_file():
            return Check("LLM cache", PASS, f"draft for the T3 shape {sig} present")
        present = len(list(cache_dir.iterdir()))
        return Check(
            "LLM cache",
            WARN,
            f"no draft for the T3 shape {sig} ({present} other entries); run `make llm-cache-seed`",
        )

    def _t3_template_sig(self) -> str:
        """The signature of the T3 failed-login shape, as the drafter will look it up.

        The signature is computed over the *peeled* text region with the source's scope, not
        over the whole line, so the only honest way to get it is to run the engine on the
        same bytes the demo sends. Hand-computing it would check a key nothing ever reads.
        """
        from demo_engine.senders import load_corpus
        from veyra_common.envelope import stamp
        from veyra_engine.engine import Engine

        event = load_corpus("authsrv_t3_failed.log")[0]
        envelope = stamp(
            event.encode("utf-8"),
            collector_id="gw_01",
            transport="http_hec_event",
            framing_method="http_body",
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            vendor="custom",
            zone="dmz",
        )
        return str(Engine().normalize(envelope).ulpf["template"]["sig"])

    def wazuh_rules(self) -> Check:
        """Ask the Wazuh manager API which of rules 100100-100130 it has loaded."""
        if self.cfg.wazuh_mode != "local":
            return Check("Wazuh rules", WARN, "Wazuh is remote; rules cannot be checked from here")
        base = self.cfg.wazuh_manager_url.rstrip("/")
        try:
            auth = httpx.get(
                f"{base}/security/user/authenticate",
                auth=(self.cfg.wazuh_api_user, self.cfg.wazuh_api_password),
                verify=False,
                timeout=10.0,
            )
            auth.raise_for_status()
            token = auth.json()["data"]["token"]
            response = httpx.get(
                f"{base}/rules",
                params={"rule_ids": ",".join(str(r) for r in WAZUH_RULE_RANGE), "limit": 500},
                headers={"Authorization": f"Bearer {token}"},
                verify=False,
                timeout=15.0,
            )
            response.raise_for_status()
        except Exception as exc:
            return Check(
                "Wazuh rules", FAIL, f"manager API unreachable: {type(exc).__name__}: {exc}"
            )
        found = {int(item["id"]) for item in response.json()["data"]["affected_items"]}
        # 100100 is the parent; 100110-100130 are its children. The demo needs the parent and
        # the brute-force rule at minimum.
        essential = {100100, 100111}
        missing = essential - found
        if missing:
            return Check(
                "Wazuh rules",
                FAIL,
                f"missing essential rule(s) {sorted(missing)}; {len(found)} loaded",
            )
        return Check(
            "Wazuh rules", PASS, f"{len(found)} VEYRA rules loaded, including 100100 and 100111"
        )

    def clock_offset(self) -> Check:
        """Compare our clock with a container's, using the ClickHouse server's now()."""
        from veyra_lineage.client import make_client

        # No database selected: the clock is readable before B1's migrations have run.
        client = make_client(self.cfg, database="")
        before = time.time()
        server_now = client.query("SELECT toUnixTimestamp64Milli(now64(3))").result_rows[0][0]
        after = time.time()
        # Charge the round trip to the measurement, not to the offset.
        midpoint = (before + after) / 2
        offset = abs(float(server_now) / 1000.0 - midpoint)
        detail = (
            f"{offset * 1000:.0f} ms against ClickHouse, limit {MAX_CLOCK_OFFSET_S * 1000:.0f} ms"
        )
        return Check("Clock offset", PASS if offset < MAX_CLOCK_OFFSET_S else FAIL, detail)

    def open_segments(self) -> Check:
        """No segment should be open for longer than twice its maximum age."""
        limit = 2 * self.cfg.segment_max_seconds
        vault = Path(self.cfg.vault_dir)
        # An open segment is written as a .part alongside the sealed .seg files.
        open_files = list(vault.glob("**/*.part")) + list(vault.glob("**/*.open"))
        now = time.time()
        stale = {
            path.name: int(now - path.stat().st_mtime)
            for path in open_files
            if now - path.stat().st_mtime > limit
        }
        if stale:
            detail = ", ".join(f"{name} open {age}s" for name, age in stale.items())
            return Check("Open segments", FAIL, f"older than {limit}s: {detail}")
        return Check("Open segments", PASS, f"{len(open_files)} open, none older than {limit}s")

    def baseline_eps(self) -> Check:
        """Is background traffic flowing within ±30% of its target?"""
        if self.baseline is None:
            return Check("Baseline EPS", WARN, "no baseline loop attached to this engine")
        if self.baseline.is_paused():
            return Check("Baseline EPS", WARN, "baseline is paused")
        target = self.baseline.target_eps()
        actual = self.baseline.total_eps()
        if target <= 0:
            return Check("Baseline EPS", WARN, "the scenario sets no baseline rate")
        drift = abs(actual - target) / target
        detail = f"{actual:.1f} eps against a {target:.1f} eps target ({drift * 100:.0f}% off)"
        return Check("Baseline EPS", PASS if drift <= BASELINE_TOLERANCE else FAIL, detail)

    def console_hotkeys(self) -> Check:
        """Has an open console registered its demo hotkeys with this engine?"""
        if self.hotkeys_seen():
            return Check("Console hotkeys", PASS, "a console has reported its demo hotkeys")
        return Check(
            "Console hotkeys",
            WARN,
            "no console has reported hotkeys; open /demo so Shift+1..6 are live",
        )


def _available_memory_gb() -> float | None:
    """Available memory in GB, or None when the platform will not say."""
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024**2
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / 1024**3
    except (ValueError, OSError, AttributeError):
        return None
