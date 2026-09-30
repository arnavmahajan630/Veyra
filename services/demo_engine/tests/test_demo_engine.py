"""Unit tests for the demo engine (B7).

These run without the stack: senders build bytes without sockets, the reset's ordering and
failure handling are exercised against fakes, and the expectation evaluators are driven
through stubs. The live acceptance criteria (a < 90 s reset, ten green `demo-auto` runs) need
the full profile and belong to the rehearsal.
"""

from __future__ import annotations

import datetime
import random
from pathlib import Path
from typing import Any

import pytest
import yaml
from demo_engine import senders
from demo_engine.app import create_app
from demo_engine.expectations import Evaluator
from demo_engine.preflight import PASS, WARN, PreflightChecker
from demo_engine.reset import ResetOrchestrator
from demo_engine.scenario import (
    ScenarioValidationError,
    load_scenario,
    resolve_eps,
    scenario_path,
)
from demo_engine.senders import TrafficSender
from demo_engine.stages import StageRunner
from demo_engine.templates import known_templates
from fastapi.testclient import TestClient

from veyra_common.settings import Settings

ENV = {"VEYRA_DEMO_EPS_BASELINE": "15"}


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, metrics_port=0)


@pytest.fixture
def sender(cfg: Settings) -> Any:
    instance = TrafficSender(cfg)
    yield instance
    instance.close()


def base_scenario() -> dict[str, Any]:
    """A minimal valid scenario, so a test can break exactly one thing at a time."""
    return {
        "scenario": "fixture",
        "seed": 7,
        "baseline": [
            {
                "name": "lnx",
                "via": "syslog_udp",
                "listener": "core-udp",
                "corpus": "linux_sshd.log",
                "eps": 2,
            }
        ],
        "stages": {
            1: {"title": "Hook", "actions": []},
            3: {
                "title": "Log storm",
                "actions": [
                    {
                        "send": {
                            "via": "syslog_udp",
                            "listener": "core-udp",
                            "corpus": "linux_sshd.log",
                            "count": 2,
                            "over_s": 0,
                        }
                    }
                ],
                "expect": [{"within_s": 1, "clickhouse": "tier=3", "gte": 1}],
            },
        },
        "auto": [{"at": 0, "stage": 1}],
    }


def write_scenario(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


# ---------------------------------------------------------------- corpus handling
def test_a_multi_line_event_stays_one_event() -> None:
    """Splitting on newlines would send the stack trace as its own event (Beat 5)."""
    events = senders.load_corpus("authsrv_t3_failed.log")
    assert events, "the T3 corpus should not be empty"
    assert all("\n  at com.x.Auth.login" in event for event in events)
    # 28 lines, 14 events: every event carries exactly one continuation line.
    assert len(events) == 14


def test_parse_corpus_treats_indented_lines_as_continuations() -> None:
    text = "first line\n  continued\n\nsecond line\n\tcontinued too\n"
    assert senders.parse_corpus(text) == ["first line\n  continued", "second line\n\tcontinued too"]


def test_file_selector_picks_one_event() -> None:
    first = senders.load_corpus("authsrv_t1_ok.log#1")
    assert len(first) == 1
    assert first[0] == senders.load_corpus("authsrv_t1_ok.log")[0]


def test_a_missing_corpus_is_an_error_not_an_empty_list() -> None:
    with pytest.raises(FileNotFoundError):
        senders.load_corpus("no_such_corpus.log")


def test_an_out_of_range_selector_is_an_error() -> None:
    with pytest.raises(IndexError):
        senders.load_corpus("authsrv_t1_ok.log#9999")


def test_filter_and_exclude_narrow_the_pool() -> None:
    denies = senders.select_events("acme_ngfw_cef.log", filter_expr="act=deny")
    assert denies and all("act=deny" in event for event in denies)
    # The baseline must not carry the brute force, or Wazuh fires 100111 before Beat 3.
    quiet = senders.select_events("linux_sshd.log", exclude_patterns=["Failed password"])
    assert quiet and not any("Failed password" in event for event in quiet)
    assert len(quiet) < len(senders.load_corpus("linux_sshd.log"))


# ---------------------------------------------------------------- timestamps
@pytest.mark.parametrize(
    "corpus",
    ["linux_sshd.log", "acme_ngfw_cef.log", "ot_historian.log", "authsrv_t3_failed.log"],
)
def test_every_corpus_format_has_its_timestamp_moved(corpus: str) -> None:
    """Each format is rewritten in its own shape, and the structure is untouched."""
    original = senders.load_corpus(corpus)[0]
    moment = datetime.datetime(2031, 3, 4, 5, 6, 7, tzinfo=datetime.UTC)
    rewritten = senders.rewrite_timestamp(original, moment)
    assert rewritten != original, f"{corpus}: the timestamp was not rewritten"
    assert rewritten.count("\n") == original.count("\n")
    assert rewritten.count(";") == original.count(";")


def test_the_ot_historian_format_is_rewritten_in_place() -> None:
    moment = datetime.datetime(2031, 3, 4, 5, 6, 7, tzinfo=datetime.UTC)
    line = senders.rewrite_timestamp(senders.load_corpus("ot_historian.log")[0], moment)
    assert line.startswith("04-03-2031 05:06:07;")


def test_the_cef_receipt_time_keeps_its_year() -> None:
    moment = datetime.datetime(2031, 3, 4, 5, 6, 7, tzinfo=datetime.UTC)
    line = senders.rewrite_timestamp(senders.load_corpus("acme_ngfw_cef.log")[0], moment)
    assert "rt=Mar 4 2031 05:06:07" in line


# ---------------------------------------------------------------- vary / hosts
def test_vary_substitutes_user_and_ip_in_both_shapes() -> None:
    line = 'user=old FAILED login from 1.1.1.1 via 10.0.0.1 {"user": "old"}'
    varied = senders.apply_vary(line, {"user": "a.sharma", "src_ip": "103.21.4.77"})
    assert "user=a.sharma" in varied
    assert "from 103.21.4.77" in varied
    assert '"user":"a.sharma"' in varied
    # The unrelated destination address is untouched.
    assert "via 10.0.0.1" in varied


def test_cef_is_wrapped_with_the_inventory_host(sender: TrafficSender) -> None:
    """CEF carries no syslog header, so the inventory has nothing to resolve without one."""
    event = sender.build_events(
        {"via": "syslog_tcp", "listener": "dmz-tcp", "corpus": "acme_ngfw_cef.log"},
        random.Random(1),
        1,
    )[0]
    assert " fw-dmz-01 CEF:0|Acme|NGFW" in event
    assert event.startswith("<134>")


def test_the_ot_historian_goes_on_the_wire_verbatim(sender: TrafficSender) -> None:
    """Its bytes are what the tier-3 goldens and the S0 parity vectors are computed from."""
    original = senders.load_corpus("ot_historian.log")[0]
    event = sender.build_events(
        {"via": "syslog_udp", "listener": "core-udp", "corpus": "ot_historian.log"},
        random.Random(1),
        1,
    )[0]
    assert not event.startswith("<")
    assert event.split(";", 1)[1] == original.split(";", 1)[1]


def test_a_host_that_could_not_take_effect_is_rejected(tmp_path: Path) -> None:
    document = base_scenario()
    document["stages"][3]["actions"] = [
        {
            "send": {
                "via": "syslog_udp",
                "listener": "core-udp",
                "corpus": "ot_historian.log",
                "host": "ot-hist-01",
            }
        }
    ]
    with pytest.raises(ScenarioValidationError, match="would have no effect"):
        load_scenario(write_scenario(tmp_path, document), env=ENV)


# ---------------------------------------------------------------- determinism
def test_the_same_seed_produces_the_same_bytes(sender: TrafficSender) -> None:
    """A reproducible demo means two runs send identical traffic."""
    action = {
        "via": "syslog_udp",
        "listener": "core-udp",
        "template": "sshd_failed",
        "vary": {"src_ip": ["45.12.3.9"]},
    }
    moment = datetime.datetime(2031, 3, 4, 5, 6, 7, tzinfo=datetime.UTC)
    first = sender.build_events(action, random.Random(42), 5)
    second = sender.build_events(action, random.Random(42), 5)
    assert first == second
    assert sender.build_events(action, random.Random(43), 5) != first
    assert all("45.12.3.9" in event for event in first)
    assert moment  # the generator stamps "now"; determinism is about the seeded parts


def test_a_generated_line_is_properly_framed(sender: TrafficSender) -> None:
    event = sender.build_events(
        {"via": "syslog_udp", "listener": "core-udp", "template": "sshd_failed"},
        random.Random(1),
        1,
    )[0]
    assert event.startswith("<86>")
    assert " core-lnx-07 sshd[" in event
    assert "Failed password for invalid user" in event


# ---------------------------------------------------------------- scenario validation
def test_the_real_scenario_loads_and_matches_the_demo_script() -> None:
    scenario = load_scenario(scenario_path("sih_main"), env=ENV)
    assert scenario.name == "sih_main"
    assert scenario.seed == 42, "an unseeded scenario is not reproducible"
    assert sorted(scenario.stages) == [1, 2, 3, 4, 5, 6]
    assert scenario.stages[3].title == "Log storm"
    # The brute force must be held back for Beat 3.
    lnx = next(stream for stream in scenario.baseline if stream.name == "ntro_lnx")
    assert "Failed password" in lnx.exclude_patterns
    assert lnx.eps == pytest.approx(7.5)


def test_eps_resolves_an_environment_expression() -> None:
    assert resolve_eps("${VEYRA_DEMO_EPS_BASELINE}*0.5", ENV) == 7.5
    assert resolve_eps(4) == 4.0
    with pytest.raises(ScenarioValidationError):
        resolve_eps("${VEYRA_NOT_SET}", {})


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["stages"].update({9: {"title": "nope", "actions": []}}), "1..6"),
        (
            lambda d: d["stages"][3]["actions"].append(
                {"send": {"via": "carrier_pigeon", "corpus": "linux_sshd.log"}}
            ),
            "unknown transport",
        ),
        (
            lambda d: d["stages"][3]["actions"].append(
                {"send": {"via": "syslog_udp", "listener": "nowhere", "corpus": "linux_sshd.log"}}
            ),
            "unknown listener",
        ),
        (
            lambda d: d["stages"][3]["actions"].append(
                {"send": {"via": "syslog_udp", "listener": "core-udp", "corpus": "missing.log"}}
            ),
            "missing corpus",
        ),
        (
            lambda d: d["stages"][3]["actions"].append(
                {
                    "send": {
                        "via": "syslog_udp",
                        "listener": "core-udp",
                        "template": "no_such_generator",
                    }
                }
            ),
            "unknown template",
        ),
        (
            lambda d: d["stages"][3]["actions"].append({"teleport": {}}),
            "unknown action",
        ),
        (
            lambda d: d["stages"][3]["expect"].append({"within_s": 5, "somethign_typoed": 1}),
            "unknown expect key",
        ),
        (lambda d: d.update({"baseline": {"actions": []}}), "list of named streams"),
    ],
)
def test_the_validator_refuses_a_scenario_that_cannot_run(
    tmp_path: Path, mutate: Any, message: str
) -> None:
    """Every one of these used to fail silently at run time, mid-demo."""
    document = base_scenario()
    mutate(document)
    with pytest.raises(ScenarioValidationError, match=message):
        load_scenario(write_scenario(tmp_path, document), env=ENV)


def test_every_template_the_scenario_names_exists() -> None:
    scenario = load_scenario(scenario_path("sih_main"), env=ENV)
    named = {
        action["send"]["template"]
        for stage in scenario.stages.values()
        for action in stage.actions
        if "send" in action and action["send"].get("template")
    }
    assert named <= known_templates()


# ---------------------------------------------------------------- expectations
def test_an_unrecognised_expectation_fails_and_never_passes() -> None:
    """The old evaluator returned True for anything it did not know."""
    outcome = Evaluator(Settings(_env_file=None)).evaluate({"within_s": 0, "made_up_key": 1})
    assert outcome.ok is False
    assert "no evaluator" in outcome.detail


def test_a_clickhouse_expectation_is_satisfied_by_a_count(monkeypatch: Any) -> None:
    evaluator = Evaluator(Settings(_env_file=None))
    monkeypatch.setattr(evaluator, "clickhouse_count", lambda where, gte: (True, f"{gte} rows"))
    outcome = evaluator.evaluate({"within_s": 1, "clickhouse": "tier=3", "gte": 8})
    assert outcome.ok
    assert outcome.label == "clickhouse: tier=3 >= 8"


def test_an_expectation_that_never_holds_times_out(monkeypatch: Any) -> None:
    evaluator = Evaluator(Settings(_env_file=None), poll_s=0.01)
    monkeypatch.setattr(evaluator, "wazuh_alert", lambda rule, ip: (False, "0 alerts"))
    outcome = evaluator.evaluate({"within_s": 0.05, "wazuh_rule": 100111, "src_ip": "45.12.3.9"})
    assert outcome.ok is False
    assert "after 0.05s" in outcome.detail
    assert outcome.label == "wazuh rule 100111 for 45.12.3.9"


# ---------------------------------------------------------------- stages
def test_a_stage_is_idempotent_while_it_runs(
    tmp_path: Path, cfg: Settings, sender: TrafficSender
) -> None:
    scenario = load_scenario(write_scenario(tmp_path, base_scenario()), env=ENV)
    runner = StageRunner(scenario, sender, cfg)
    runner._states[3] = "running"  # as if a trigger were in flight
    assert runner.run_stage(3) is False, "a repeat trigger must not start a second run"


def test_an_unknown_stage_is_rejected(tmp_path: Path, cfg: Settings, sender: TrafficSender) -> None:
    scenario = load_scenario(write_scenario(tmp_path, base_scenario()), env=ENV)
    runner = StageRunner(scenario, sender, cfg)
    with pytest.raises(KeyError):
        runner.run_stage(5)


def test_a_stage_whose_expectation_fails_is_reported_failed(
    tmp_path: Path, cfg: Settings, sender: TrafficSender, monkeypatch: Any
) -> None:
    scenario = load_scenario(write_scenario(tmp_path, base_scenario()), env=ENV)
    evaluator = Evaluator(cfg, poll_s=0.01)
    monkeypatch.setattr(evaluator, "clickhouse_count", lambda where, gte: (False, "0 rows"))
    # The send goes nowhere in a test; what matters is that the verdict is not "done".
    monkeypatch.setattr(sender, "dispatch_action", lambda *a, **k: 0)
    runner = StageRunner(scenario, sender, cfg, evaluator)
    runner.run_stage(3, block=True)
    status = runner.get_status()
    assert status["state"] == "failed"
    assert status["results"] == {"clickhouse: tier=3 >= 1": False}


# ---------------------------------------------------------------- reset
def test_reset_reports_a_failed_step_instead_of_claiming_success(
    cfg: Settings, monkeypatch: Any
) -> None:
    """The old orchestrator swallowed every error and always returned ok: true."""
    reset = ResetOrchestrator(None, cfg)

    def boom() -> str:
        raise RuntimeError("clickhouse is down")

    monkeypatch.setattr(reset, "_control_plane", lambda: "reseeded")
    monkeypatch.setattr(reset, "_kafka_topics", lambda: "topics")
    monkeypatch.setattr(reset, "_clickhouse", boom)
    monkeypatch.setattr(reset, "_immudb", lambda: "db")
    monkeypatch.setattr(reset, "_wazuh", lambda: "wazuh")
    monkeypatch.setattr(reset, "_restart_consumers", lambda: "restarted")
    monkeypatch.setattr(reset, "_resume_and_warm", lambda: "warm")

    result = reset.execute_reset()
    assert result["ok"] is False
    failed = [step for step in result["steps"] if not step["ok"]]
    assert len(failed) == 1
    assert "clickhouse is down" in failed[0]["detail"]
    # Later steps still ran, so one broken dependency does not hide the rest.
    assert [step["name"] for step in result["steps"]][-1].startswith("Resume baseline")


def test_reset_runs_its_steps_in_the_documented_order(cfg: Settings, monkeypatch: Any) -> None:
    order: list[str] = []
    reset = ResetOrchestrator(None, cfg)
    for name in (
        "_control_plane",
        "_kafka_topics",
        "_clickhouse",
        "_immudb",
        "_wazuh",
        "_restart_consumers",
        "_resume_and_warm",
    ):
        monkeypatch.setattr(reset, name, (lambda n: lambda: (order.append(n), n)[1])(name))
    reset.execute_reset()
    assert order == [
        "_control_plane",
        "_kafka_topics",
        "_clickhouse",
        "_immudb",
        "_wazuh",
        "_restart_consumers",
        "_resume_and_warm",
    ]


def test_the_vault_wipe_keeps_the_signing_keys(cfg: Settings) -> None:
    """Losing data/keys/ would make every previously signed root unverifiable."""
    vault = Path(cfg.vault_dir) / "raw.custom" / "0"
    vault.mkdir(parents=True)
    (vault / "seg_one.seg").write_bytes(b"segment")
    roots = Path(cfg.vault_dir) / "roots"
    roots.mkdir(parents=True)
    (roots / "ledger.ndjson").write_text("{}\n")
    keys = Path(cfg.keys_dir)
    keys.mkdir(parents=True)
    (keys / "signing.key").write_text("secret")

    detail = ResetOrchestrator(None, cfg)._vault()
    assert "1 segment(s) removed" in detail
    assert not (vault / "seg_one.seg").exists()
    assert not roots.exists()
    assert (keys / "signing.key").read_text() == "secret"


def test_reset_status_is_real_rather_than_a_hardcoded_ready(cfg: Settings) -> None:
    reset = ResetOrchestrator(None, cfg)
    assert reset.status()["ready"] is True
    reset.state.running = True
    assert reset.status()["ready"] is False


# ---------------------------------------------------------------- preflight
def test_preflight_measures_every_row(cfg: Settings) -> None:
    rows = PreflightChecker(cfg).check_all()
    names = [row["check"] for row in rows]
    assert len(rows) == 10, "the phase file lists ten checks"
    for expected in ("Containers", "Host memory", "Ollama model", "LLM cache", "Clock offset"):
        assert expected in names
    # Nothing may report the fixed strings the old version returned.
    assert not any("< 50 ms host/container skew" in row["detail"] for row in rows)
    assert not any(row["detail"].endswith("check passed") for row in rows)


def test_a_stopped_ollama_warns_and_never_fails(cfg: Settings, monkeypatch: Any) -> None:
    """B7 AC4: with Ollama down the drafter falls back to its cache, so the demo still runs."""
    import httpx

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", refuse)
    row = PreflightChecker(cfg).ollama()
    assert row.status == WARN
    assert "cache" in row.detail


def test_the_llm_cache_check_uses_the_key_the_drafter_looks_up(cfg: Settings) -> None:
    checker = PreflightChecker(cfg)
    sig = checker._t3_template_sig()
    assert sig.startswith("t_")
    cache = Path(cfg.llm_cache_dir)
    cache.mkdir(parents=True)
    (cache / f"{sig}.json").write_text("{}")
    assert checker.llm_cache().status == PASS


def test_a_check_that_raises_is_a_failure_not_a_pass(cfg: Settings, monkeypatch: Any) -> None:
    checker = PreflightChecker(cfg)
    monkeypatch.setattr(
        checker, "disk_space", lambda: (_ for _ in ()).throw(RuntimeError("no /proc"))
    )
    rows = checker.check_all()
    broken = [row for row in rows if row["check"] == "Disk space"]
    assert broken and broken[0]["status"] == "FAIL"


# ---------------------------------------------------------------- HTTP surface
@pytest.fixture
def client(cfg: Settings) -> Any:
    with TestClient(create_app(cfg=cfg, start_baseline=False)) as test_client:
        yield test_client


def test_the_api_matches_if_api_demo(client: TestClient) -> None:
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["scenario"] == "sih_main"

    scenario = client.get("/scenario").json()
    assert scenario["seed"] == 42
    assert scenario["stages"]["3"]["expects"], "the console shows each stage's expectations"
    # Stage 2 pre-fills the onboarding form with T1 and T2 — never the T3 drift shape.
    samples = scenario["stages"]["2"]["samples"]
    assert len(samples) == 3
    assert not any("FAILED login" in sample for sample in samples)

    assert client.get("/stage/status").json()["state"] == "idle"
    assert client.get("/reset/status").json()["ready"] is True
    assert client.post("/stage/9").status_code == 400
    assert client.get("/tamper/active").json()["modes"] == [
        "naive_flip",
        "insider_rewrite",
        "segment_delete",
        "root_rewrite",
    ]


def test_an_unknown_tamper_mode_is_rejected(client: TestClient) -> None:
    response = client.post("/tamper", json={"mode": "unplug_the_laptop"})
    assert response.status_code in (400, 409)


def test_the_console_can_report_its_hotkeys(client: TestClient) -> None:
    """Preflight's last check asks whether a console has registered them."""
    response = client.post("/hotkeys", json={"combos": ["Shift+1", "Shift+T"]})
    assert response.json() == {"ok": True, "combos": ["Shift+1", "Shift+T"]}
    rows = {row["check"]: row for row in client.get("/preflight").json()}
    assert rows["Console hotkeys"]["status"] == "PASS"


def test_a_broken_scenario_fails_at_startup(tmp_path: Path, cfg: Settings) -> None:
    """Better than answering happily while holding no stages."""
    broken = write_scenario(tmp_path, {"scenario": "broken", "stages": {9: {"title": "nope"}}})
    with pytest.raises(ScenarioValidationError):
        create_app(broken, cfg=cfg, start_baseline=False)
