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


@dataclass
class Outcome:
    clause: dict[str, Any]
    ok: bool
    detail: str
    seconds: float

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
        return str(self.clause)

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
            "seconds": round(self.seconds, 2),
        }


class Probe(Protocol):
    """One question against a live system. Returns (satisfied, detail)."""

    def __call__(self) -> tuple[bool, str]: ...


class Evaluator:
    """Polls each clause until it holds or its deadline passes."""

    def __init__(self, cfg: Settings | None = None, poll_s: float = 1.0) -> None:
        self.cfg = cfg or Settings()
        self.poll_s = poll_s

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
        """Ask the control API whether a drift item is open for this source."""
        try:
            response = httpx.get(
                f"{self.cfg.control_api_url.rstrip('/')}/drift",
                params={"state": "open", "source_id": source_id},
                timeout=5.0,
            )
            response.raise_for_status()
        except Exception as exc:
            return False, f"control-api drift query failed: {exc}"
        payload = response.json()
        items = payload if isinstance(payload, list) else payload.get("items", [])
        return len(items) > 0, f"{len(items)} open drift item(s) for {source_id}"

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
