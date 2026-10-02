"""Demo scenario loader and validator (B7).

A scenario is loaded once, at start-up, and every mistake in it is raised there: an unknown
corpus file, an unknown generator name, a stage outside 1..6, an unknown listener or an
`expect` clause nobody can evaluate. The alternative is a stage that silently sends nothing
halfway through a three-minute demo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from demo_engine.senders import CORPUS_DIR, SELF_IDENTIFYING, load_corpus
from demo_engine.templates import known_templates

REPO_DIR = Path(__file__).resolve().parents[4]
SCENARIOS_DIR = REPO_DIR / "demo" / "scenarios"

LISTENERS = frozenset({"dmz-udp", "dmz-tcp", "core-udp", "core-tcp"})
TRANSPORTS = frozenset({"syslog_udp", "syslog_tcp", "http_hec_event"})
ACTION_KINDS = frozenset({"send", "drift_flush", "resend_if_no_drift", "prefill_onboarding"})
# Every `expect` key an evaluator knows. An unknown one must fail at load, because at run
# time an unrecognised expectation silently "passes" and hides a broken stage.
EXPECT_KEYS = frozenset(
    {
        "within_s",
        "clickhouse",
        "gte",
        "wazuh_rule",
        "src_ip",
        "drift_open_for",
        # A contract version is active in the control plane: `contract_active: authsrv@2`.
        "contract_active",
        # An event matching this search term verifies end to end: `evidence_verifies: a.sharma`.
        "evidence_verifies",
    }
)

_ENV_EXPR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}(?:\s*\*\s*([0-9.]+))?")


class ScenarioValidationError(ValueError):
    """A scenario file that cannot be run as written."""


def resolve_eps(value: Any, env: dict[str, str] | None = None) -> float:
    """Resolve an `eps:` value, which may be ``${VEYRA_DEMO_EPS_BASELINE}*0.5``."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = _ENV_EXPR.fullmatch(text)
    if match is None:
        try:
            return float(text)
        except ValueError:
            raise ScenarioValidationError(f"cannot read eps value {value!r}") from None
    source = env if env is not None else dict(os.environ)
    name, factor = match.group(1), match.group(2)
    if name not in source:
        raise ScenarioValidationError(f"eps references {name}, which is not set")
    return float(source[name]) * (float(factor) if factor else 1.0)


@dataclass(frozen=True)
class BaselineStream:
    """One continuous background traffic stream."""

    name: str
    via: str
    listener: str | None
    corpus: str
    eps: float
    exclude_patterns: list[str] = field(default_factory=list)

    def as_action(self) -> dict[str, Any]:
        """The `send`-shaped config this stream paces, for the sender to dispatch."""
        return {
            "via": self.via,
            "listener": self.listener,
            "corpus": self.corpus,
            "exclude_patterns": list(self.exclude_patterns),
        }


@dataclass(frozen=True)
class Stage:
    number: int
    title: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    expect: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int
    baseline: list[BaselineStream]
    stages: dict[int, Stage]
    auto_script: list[dict[str, Any]]
    # The search term that finds Beat 5's event. The console's tamper panel and the auto
    # flow both read it from here, instead of each hardcoding the demo's user name.
    tamper_query: str = "a.sharma"

    @property
    def baseline_actions(self) -> list[dict[str, Any]]:
        return [stream.as_action() for stream in self.baseline]


def scenario_path(name: str = "sih_main") -> Path:
    return SCENARIOS_DIR / f"{name}.yaml"


def _check_corpus(corpus: str, where: str) -> None:
    try:
        load_corpus(corpus)
    except FileNotFoundError:
        available = ", ".join(sorted(p.name for p in CORPUS_DIR.glob("*.log")))
        raise ScenarioValidationError(
            f"{where} references missing corpus {corpus!r}; available: {available}"
        ) from None
    except IndexError as exc:
        raise ScenarioValidationError(f"{where}: {exc}") from None


def _check_send(cfg: dict[str, Any], where: str) -> None:
    via = cfg.get("via")
    if via not in TRANSPORTS:
        raise ScenarioValidationError(
            f"{where} uses unknown transport {via!r}; known: {', '.join(sorted(TRANSPORTS))}"
        )
    if via != "http_hec_event":
        listener = cfg.get("listener")
        if listener not in LISTENERS:
            raise ScenarioValidationError(
                f"{where} uses unknown listener {listener!r}; known: {', '.join(sorted(LISTENERS))}"
            )
    corpus, template = cfg.get("corpus"), cfg.get("template")
    if not corpus and not template:
        raise ScenarioValidationError(f"{where} names neither a corpus nor a template")
    if corpus and template:
        raise ScenarioValidationError(f"{where} names both a corpus and a template")
    if corpus:
        _check_corpus(corpus, where)
        if cfg.get("host") and corpus.partition("#")[0] in SELF_IDENTIFYING:
            raise ScenarioValidationError(
                f"{where} sets host on {corpus!r}, which carries its own device field and is "
                "sent byte-for-byte; the host would have no effect"
            )
    if template and template not in known_templates():
        raise ScenarioValidationError(
            f"{where} uses unknown template {template!r}; "
            f"known: {', '.join(sorted(known_templates()))}"
        )


def _check_expect(clause: dict[str, Any], where: str) -> None:
    unknown = set(clause) - EXPECT_KEYS
    if unknown:
        raise ScenarioValidationError(
            f"{where} has unknown expect key(s) {', '.join(sorted(unknown))}; "
            f"known: {', '.join(sorted(EXPECT_KEYS))}"
        )
    if "clickhouse" in clause and "gte" not in clause:
        raise ScenarioValidationError(f"{where} has a clickhouse clause with no `gte` threshold")


def env_from(cfg: Any) -> dict[str, str]:
    """The variables a scenario may interpolate, taken from Settings.

    Settings is the single source of truth (profiles/*.env feed it), so a scenario resolves
    the same way whether or not the variable also happens to be exported in the shell.
    """
    return {**os.environ, "VEYRA_DEMO_EPS_BASELINE": str(cfg.demo_eps_baseline)}


def load_scenario(
    path: Path | None = None,
    *,
    name: str | None = None,
    env: dict[str, str] | None = None,
    cfg: Any = None,
) -> Scenario:
    """Read and validate one scenario. Raises on anything that cannot be run."""
    if env is None and cfg is not None:
        env = env_from(cfg)
    target = path or scenario_path(name or "sih_main")
    if not Path(target).is_file():
        available = ", ".join(sorted(p.stem for p in SCENARIOS_DIR.glob("*.yaml"))) or "none"
        raise FileNotFoundError(f"no scenario at {target} (available: {available})")

    data = yaml.safe_load(Path(target).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ScenarioValidationError(f"{target}: a scenario must be a mapping")

    scenario_name = str(data.get("scenario") or Path(target).stem)
    try:
        seed = int(data.get("seed", 0))
    except (TypeError, ValueError):
        raise ScenarioValidationError(f"{target}: seed must be an integer") from None

    # ------------------------------------------------------------------ baseline
    raw_baseline = data.get("baseline") or []
    if not isinstance(raw_baseline, list):
        raise ScenarioValidationError(
            f"{target}: baseline must be a list of named streams (see B7_demo_engine.md)"
        )
    baseline: list[BaselineStream] = []
    for index, entry in enumerate(raw_baseline):
        if not isinstance(entry, dict):
            raise ScenarioValidationError(f"{target}: baseline[{index}] must be a mapping")
        where = f"baseline stream {entry.get('name', index)}"
        corpus = entry.get("corpus")
        if not corpus:
            raise ScenarioValidationError(f"{where} names no corpus")
        _check_send({**entry, "corpus": corpus}, where)
        baseline.append(
            BaselineStream(
                name=str(entry.get("name", f"stream_{index}")),
                via=str(entry["via"]),
                listener=entry.get("listener"),
                corpus=str(corpus),
                eps=resolve_eps(entry.get("eps", 1), env),
                exclude_patterns=[str(p) for p in entry.get("exclude_patterns", [])],
            )
        )

    # -------------------------------------------------------------------- stages
    raw_stages = data.get("stages") or {}
    if not isinstance(raw_stages, dict):
        raise ScenarioValidationError(f"{target}: stages must be a mapping of stage numbers")

    stages: dict[int, Stage] = {}
    for key, value in raw_stages.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            raise ScenarioValidationError(f"{target}: {key!r} is not a stage number") from None
        if not 1 <= number <= 6:
            raise ScenarioValidationError(f"{target}: stage number must be 1..6, got {number}")
        if not isinstance(value, dict):
            raise ScenarioValidationError(f"{target}: stage {number} must be a mapping")

        actions = list(value.get("actions") or [])
        for position, action in enumerate(actions):
            if not isinstance(action, dict) or len(action) != 1:
                raise ScenarioValidationError(
                    f"stage {number} action {position} must be a single-key mapping"
                )
            kind, cfg = next(iter(action.items()))
            if kind not in ACTION_KINDS:
                raise ScenarioValidationError(
                    f"stage {number} action {position}: unknown action {kind!r}; "
                    f"known: {', '.join(sorted(ACTION_KINDS))}"
                )
            where = f"stage {number} action {position} ({kind})"
            if kind == "send":
                _check_send(cfg or {}, where)
            elif kind == "resend_if_no_drift":
                _check_corpus(str((cfg or {}).get("corpus", "")), where)
            elif kind == "prefill_onboarding":
                for sample in (cfg or {}).get("samples", []):
                    _check_corpus(str(sample), where)

        expect = list(value.get("expect") or [])
        for position, clause in enumerate(expect):
            if not isinstance(clause, dict):
                raise ScenarioValidationError(f"stage {number} expect {position} must be a mapping")
            _check_expect(clause, f"stage {number} expect {position}")

        stages[number] = Stage(
            number=number,
            title=str(value.get("title", f"Stage {number}")),
            actions=actions,
            expect=expect,
        )

    auto_script = list(data.get("auto") or [])
    for position, step in enumerate(auto_script):
        if not isinstance(step, dict) or "at" not in step:
            raise ScenarioValidationError(f"{target}: auto[{position}] needs an `at` offset")
        if "stage" in step and int(step["stage"]) not in stages:
            raise ScenarioValidationError(
                f"{target}: auto[{position}] triggers stage {step['stage']}, which is not defined"
            )
        # An `api:` step may carry its own expectations. A stage's belong to the stage; an api
        # flow had nowhere to put them, so what it proved was only ever its own exception.
        for index, clause in enumerate(step.get("expect") or []):
            if not isinstance(clause, dict):
                raise ScenarioValidationError(
                    f"{target}: auto[{position}] expect {index} must be a mapping"
                )
            _check_expect(clause, f"{target}: auto[{position}] expect {index}")

    tamper = data.get("tamper") or {}
    if not isinstance(tamper, dict):
        raise ScenarioValidationError(f"{target}: tamper must be a mapping")

    return Scenario(
        name=scenario_name,
        seed=seed,
        baseline=baseline,
        stages=stages,
        auto_script=auto_script,
        tamper_query=str(tamper.get("query") or "a.sharma"),
    )
