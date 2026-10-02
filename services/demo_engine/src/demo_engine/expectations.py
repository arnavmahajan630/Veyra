"""Evaluators for a stage's ``expect`` clauses (B7).

Every clause is a question about the live system with a deadline: "within 25 s, are there at
least 8 tier-3 events from this source?". An expectation that cannot be evaluated is a
**failure**, never a pass — a clause that quietly returns True is worse than no clause,
because it reports a broken stage as healthy.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from veyra_common.settings import Settings

log = logging.getLogger(__name__)

# The probes read the control plane as the platform admin, which can see every tenant.
ADMIN = "admin@veyra"


@dataclass
class Outcome:
    clause: dict[str, Any]
    ok: bool
    detail: str
    seconds: float
    # False when the clause cannot apply to this profile at all — a Wazuh alert on a profile
    # that does not run Wazuh. Those are reported, and do not fail the stage, but they are
    # never counted as having passed either. Anything that *could* be checked and was not is
    # still a failure.
    applicable: bool = True

    @property
    def label(self) -> str:
        """A short, stable description, used as the key the console shows."""
        if "clickhouse" in self.clause:
            return f"clickhouse: {self.clause['clickhouse']} >= {self.clause.get('gte', 1)}"
        if "wazuh_rule" in self.clause:
            target = self.clause.get("src_ip", "any source")
            return f"wazuh rule {self.clause['wazuh_rule']} for {target}"
        if "drift_open_for" in self.clause:
            return f"drift open for {self.clause['drift_open_for']}"
        if "contract_active" in self.clause:
            return f"contract {self.clause['contract_active']} active"
        if "evidence_verifies" in self.clause:
            return f"an event matching {self.clause['evidence_verifies']!r} verifies"
        return str(self.clause)

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
            "seconds": round(self.seconds, 2),
            "applicable": self.applicable,
        }


class Probe(Protocol):
    """One question against a live system. Returns (satisfied, detail)."""

    def __call__(self) -> tuple[bool, str]: ...


class Evaluator:
    """Polls each clause until it holds or its deadline passes."""

    def __init__(self, cfg: Settings | None = None, poll_s: float = 1.0) -> None:
        self.cfg = cfg or Settings()
        self.poll_s = poll_s
        self._control: httpx.Client | None = None

    def close(self) -> None:
        if self._control is not None:
            self._control.close()
            self._control = None

    def _control_session(self) -> httpx.Client:
        """A signed-in control-API client, created once and reused."""
        if self._control is None:
            client = httpx.Client(base_url=self.cfg.control_api_url.rstrip("/"), timeout=10.0)
            response = client.post(
                "/auth/login",
                json={"email": ADMIN, "password": self.cfg.demo_password},
            )
            if response.status_code >= 400:
                client.close()
                raise RuntimeError(
                    f"control-api login as {ADMIN} -> {response.status_code}: {response.text[:200]}"
                )
            self._control = client
        return self._control

    # ------------------------------------------------------------------ probes
    def clickhouse_count(self, where: str, gte: int) -> tuple[bool, str]:
        """Count rows in the lineage index matching a WHERE clause."""
        query = f"SELECT count() FROM {self.cfg.clickhouse_db}.norm_lineage WHERE {where}"
        try:
            response = httpx.post(self.cfg.clickhouse_url, content=query, timeout=5.0)
            response.raise_for_status()
        except Exception as exc:
            return False, f"ClickHouse query failed: {exc}"
        try:
            count = int(response.text.strip())
        except ValueError:
            return False, f"ClickHouse returned {response.text.strip()[:80]!r}, not a count"
        return count >= gte, f"{count} row(s), needed >= {gte}"

    def wazuh_applies(self) -> bool:
        """Whether this profile runs a Wazuh that could hold an alert."""
        return str(getattr(self.cfg, "wazuh_mode", "local")) == "local"

    def wazuh_alert(self, rule_id: int, src_ip: str | None) -> tuple[bool, str]:
        """Search the Wazuh indexer for today's alerts from one rule."""
        must: list[dict[str, Any]] = [{"term": {"rule.id": str(rule_id)}}]
        if src_ip:
            # The routed event carries the attacker IP as an OCSF field and an observable.
            must.append(
                {
                    "multi_match": {
                        "query": src_ip,
                        "fields": ["data.src_endpoint.ip", "data.observables.value", "full_log"],
                    }
                }
            )
        body = {"size": 1, "query": {"bool": {"must": must}}}
        url = f"{self.cfg.wazuh_indexer_url.rstrip('/')}/wazuh-alerts-*/_search"
        try:
            response = httpx.post(
                url,
                json=body,
                auth=(self.cfg.wazuh_indexer_user, self.cfg.wazuh_indexer_password),
                verify=False,
                timeout=5.0,
            )
            response.raise_for_status()
        except Exception as exc:
            return False, f"Wazuh indexer search failed: {exc}"
        hits = int(response.json().get("hits", {}).get("total", {}).get("value", 0))
        target = f" for {src_ip}" if src_ip else ""
        return hits > 0, f"{hits} alert(s) for rule {rule_id}{target}"

    def drift_open(self, source_id: str) -> tuple[bool, str]:
        """Ask the control API whether a drift item is open for this source.

        ``GET /drift`` is behind ``current_principal``, so the probe signs in first and reuses
        the session for every later poll. Without this the clause answers 401 forever, which
        reads as "no drift" and fails the stage for the wrong reason.
        """
        try:
            client = self._control_session()
            response = client.get(
                "/drift", params={"state": "open", "source_id": source_id}, timeout=5.0
            )
            if response.status_code == 401:  # the session expired or a reset dropped it
                self._control = None
                client = self._control_session()
                response = client.get(
                    "/drift", params={"state": "open", "source_id": source_id}, timeout=5.0
                )
            response.raise_for_status()
        except Exception as exc:
            return False, f"control-api drift query failed: {exc}"
        payload = response.json()
        items = payload if isinstance(payload, list) else payload.get("items", [])
        return len(items) > 0, f"{len(items)} open drift item(s) for {source_id}"

    def contract_active(self, ref: str) -> tuple[bool, str]:
        """Is ``<contract_id>@<version>`` active in the control plane?"""
        contract_id, _, wanted = ref.partition("@")
        try:
            client = self._control_session()
            response = client.get(f"/contracts/{contract_id}")
            if response.status_code == 404:
                return False, f"contract {contract_id} does not exist"
            response.raise_for_status()
        except Exception as exc:
            return False, f"control-api contract query failed: {exc}"
        versions = response.json().get("versions") or []
        active = [str(v.get("version")) for v in versions if v.get("state") == "active"]
        if wanted:
            return wanted in active, f"active version(s) {active}, wanted {wanted}"
        return bool(active), f"active version(s) {active}"

    def evidence_verifies(self, query: str) -> tuple[bool, str]:
        """Does an event matching ``query`` verify end to end right now?"""
        base = self.cfg.evidence_api_url.rstrip("/")
        try:
            hits = (
                httpx.get(f"{base}/lineage/search", params={"q": query, "limit": 10}, timeout=15.0)
                .json()
                .get("hits")
                or []
            )
        except Exception as exc:
            return False, f"lineage search failed: {exc}"
        if not hits:
            return False, f"no event matches {query!r}"
        pending: list[str] = []
        for hit in hits:
            uid = str(hit["event_uid"])
            try:
                report = httpx.get(f"{base}/evidence/{uid}/verify", timeout=30.0).json()
            except Exception as exc:
                return False, f"verify {uid} failed: {exc}"
            if report.get("verified"):
                return True, f"{uid} verified"
            steps = report.get("steps", [])
            pending = [s["id"] for s in steps if s.get("status") == "pending_seal"]
        return False, f"nothing verified yet; newest is waiting on {pending}"

    # ------------------------------------------------------------------ dispatch
    def probe_for(self, clause: dict[str, Any]) -> Probe:
        """The probe for one clause. An unrecognised clause raises, it does not pass."""
        if "clickhouse" in clause:
            where, gte = str(clause["clickhouse"]), int(clause.get("gte", 1))
            return lambda: self.clickhouse_count(where, gte)
        if "wazuh_rule" in clause:
            rule_id = int(clause["wazuh_rule"])
            src_ip = clause.get("src_ip")
            return lambda: self.wazuh_alert(rule_id, str(src_ip) if src_ip else None)
        if "drift_open_for" in clause:
            source_id = str(clause["drift_open_for"])
            return lambda: self.drift_open(source_id)
        if "contract_active" in clause:
            ref = str(clause["contract_active"])
            return lambda: self.contract_active(ref)
        if "evidence_verifies" in clause:
            query = str(clause["evidence_verifies"])
            return lambda: self.evidence_verifies(query)
        raise ValueError(
            f"no evaluator for expectation {clause!r}; an expectation that cannot be checked "
            "must fail, not pass"
        )

    def evaluate(self, clause: dict[str, Any]) -> Outcome:
        started = time.monotonic()
        try:
            probe = self.probe_for(clause)
        except ValueError as exc:
            return Outcome(clause, False, str(exc), 0.0)

        if "wazuh_rule" in clause and not self.wazuh_applies():
            # The laptop profile runs no Wazuh: the router still writes its NDJSON sink, but
            # nothing indexes it, so there is no alert to find. Say that instead of failing a
            # beat for a capability the profile does not have.
            return Outcome(
                clause,
                ok=False,
                detail=f"VEYRA_WAZUH_MODE={self.cfg.wazuh_mode}: no Wazuh to hold an alert",
                seconds=0.0,
                applicable=False,
            )

        within_s = float(clause.get("within_s", 15.0))
        deadline = started + within_s
        detail = "not evaluated"
        while True:
            ok, detail = probe()
            elapsed = time.monotonic() - started
            if ok:
                return Outcome(clause, True, detail, elapsed)
            if time.monotonic() >= deadline:
                return Outcome(clause, False, f"{detail} (after {within_s:g}s)", elapsed)
            time.sleep(self.poll_s)
