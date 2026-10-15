"""Free fixtures through the pinned ARE tools, Judge and event loop.

Reference-derived actions below are TEST OUTPUT FIXTURES, never a real model's
inputs and never evidence of DeepSeek performance.
"""
from copy import deepcopy
import json
import sys
from pathlib import Path
from threading import Timer
from types import SimpleNamespace

import pytest

from security_adaptive_mas.config import ROOT
from security_adaptive_mas.are_adapter import import_benchmark_scenario, official_runner
from security_adaptive_mas.deepseek import DeepSeekClient, RunBudget
from security_adaptive_mas.trace import TraceLogger
from security_adaptive_mas.orchestration.configuration import load_design, project_config
from security_adaptive_mas.orchestration.session import ModularSession
from security_adaptive_mas.run_framework import save


def response(content):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class C1Fixture:
    def __init__(self, mode):
        self.mode = mode
        self.session = self.scenario = self.env = None
        self.index = 0
        self.reply_inputs = []
        self.watchdog_fired = False

    def complete(self, **kwargs):
        if kwargs.get("response_format") == {"type": "json_object"}:
            return response(json.dumps({"suspicion": "not_supported", "reason_evidence_ids": [],
                                        "possible_goal_deviation": "free fixture"}))
        system = kwargs["messages"][0]["content"]
        if self.mode == "judge_unknown" and "<subtask>" in system:
            return response("<subtask>Explicit free fixture user notification</subtask>")
        if self.mode == "judge_unknown" and ("[[Success]]" in system or "[[True]]" in system):
            return response("Explicit unparseable Judge fixture; no verdict")
        # Exact soft-argument equality uses ARE's deterministic path. A differing
        # Judge request is never silently answered with a fabricated success.
        if kwargs["messages"][0]["content"].startswith("Assess whether"):
            raise AssertionError("unexpected risk prompt routing")
        self.reply_inputs.append(deepcopy(kwargs["messages"]))
        from are.simulation.scenarios.scenario import Scenario
        actions = [e for e in self.scenario.serialized_events if e.event_type == "AGENT"]
        if self.mode == "missing_first_action":
            actions = [e for e in actions if e.action.function != "add_calendar_event"]
        if self.mode == "incomplete_second_turn":
            actions = actions[:3]
        if self.index >= len(actions):
            action = {"action": "AgentUserInterface__send_message_to_user",
                      "action_input": {"content": "Free fixture second turn ended"}}
        else:
            event = actions[self.index]
            arguments = {a.name: Scenario._parse_parameter_value(a.value, a.value_type)
                         for a in event.action.args}
            for name, value in arguments.items():
                if isinstance(value, str) and value.startswith("{{"):
                    # Read the REAL result of the earlier fixture tool call,
                    # not the Oracle reference run's return value.
                    oracle_id = value[2:-2]
                    earlier = next(e for e in actions if e.event_id == oracle_id)
                    tool = earlier.action.app + "__" + earlier.action.function
                    actual = next(c for c in self.session.calls.values() if c["tool"] == tool)
                    arguments[name] = self.session.typed_snapshots[actual["result_ref"].key]
            if self.mode == "wrong_first_argument" and event.action.function == "add_calendar_event":
                arguments["start_datetime"] = "2024-10-26 00:00:00"
            if self.mode == "judge_unknown" and event.action.function == "send_message_to_user":
                arguments["content"] = "Explicit free paraphrase for Judge contract testing"
            action = {"action": event.action.app + "__" + event.action.function,
                      "action_input": arguments}
        self.index += 1
        return response("Thought: Explicit free test fixture\nAction: " + json.dumps(action, ensure_ascii=False))

    def emergency_stop(self):
        self.watchdog_fired = True
        if self.env is not None:
            self.env.resume()
            self.env.stop()


def run_fixture(directory, mode, *, plain_idle=False):
    directory.mkdir(parents=True, exist_ok=False)
    design = load_design(ROOT / "configs/research_v0_6.yaml")
    config = project_config(design)
    fixture = C1Fixture(mode)
    trace = TraceLogger(directory / "framework_trace.jsonl")
    client = DeepSeekClient(RunBudget(trace, 100, 800000, .4), completion_fn=fixture.complete)
    session = ModularSession(design, trace, client)
    fixture.session = session
    if plain_idle:
        from security_adaptive_mas.are_adapter import import_official_scenario
        scenario = import_official_scenario(config.select("scenario_universe_22_0421h2"), config.are_root,
            filesystem=design["paths"]["gaia2_filesystem"], sandbox=directory / "sandbox")
    else:
        scenario = import_benchmark_scenario(config.select("scenario_universe_22_0421h2"), config.are_root,
            client=client, filesystem=design["paths"]["gaia2_filesystem"], sandbox=directory / "sandbox",
            on_error=session.stop_episode)
    fixture.scenario = scenario
    from security_adaptive_mas.adapters.are_modular import make_modular_agent_builder
    from security_adaptive_mas.are_adapter import BenchmarkLifecycle
    lifecycle = BenchmarkLifecycle(trace, client, idle_wait_seconds=.2 if plain_idle else 5)
    builder = make_modular_agent_builder(session, client, lifecycle=lifecycle)
    original_build = builder.build
    def capture(*args, **kwargs):
        fixture.env = kwargs.get("env") or args[1]
        return original_build(*args, **kwargs)
    builder.build = capture
    from are.simulation.scenarios.config import ScenarioRunnerConfig
    watchdog = Timer(60, fixture.emergency_stop)
    watchdog.start()
    try:
        result = official_runner(builder).run(ScenarioRunnerConfig(agent="default", model="deepseek-flash",
            model_provider="deepseek", endpoint="https://api.deepseek.com", oracle=False, export=True,
            max_turns=20, output_dir=str(directory / "official_trace")), scenario)
    finally:
        watchdog.cancel()
        if fixture.env is not None:
            fixture.env.resume()
            fixture.env.stop()
            fixture.env.thread.join(timeout=5)
    assert not fixture.watchdog_fired, "Free fixture emergency stop fired; not a normal completion"
    events = fixture.env.event_log.list_view()
    judge = getattr(scenario, "judge", None)
    save(directory / "evidence.json", {"fixture_mode": mode, "paid_api_requests": 0,
        "native_turns": scenario.nb_turns, "max_turns": 20, "actual_turns": session.turns,
        "official_success": result.success, "exception": str(result.exception) if result.exception else None,
        "official_export": str(result.export_path), "judge_turn_index": judge.state.turn_idx if judge else None,
        "judge_last_success": judge.state.last_turn_success if judge else None,
        "event_mapping": judge.state.agent_id_to_oracle_id if judge else None,
        "lifecycle_stop_reason": lifecycle.stop_reason, "lifecycle_stop_snapshot": lifecycle.stop_snapshot,
        "requests_by_purpose": client.budget.requests_by_purpose,
        "events": [e.to_dict() for e in events]})
    return fixture, result, events, client


def test_gate_c_idle_guard_keeps_task_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "free-fixture-only")
    # Deliberately reproduce the old plain-import idle path, without altering
    # the dataset. It is not claimed as a benchmark success.
    fixture, result, _, client = run_fixture(tmp_path / "idle", "correct", plain_idle=True)
    from security_adaptive_mas.are_adapter import benchmark_evaluation
    verdict = benchmark_evaluation(fixture.scenario, result, interrupted=True)
    evidence = json.loads((tmp_path / "idle/evidence.json").read_text(encoding="utf-8"))
    assert evidence["lifecycle_stop_reason"] == "INDETERMINATE_IDLE"
    assert verdict["legitimate_task_completed"] == "unknown"
    assert not verdict["benchmark_judge_completed"]
    assert client.budget.request_reservation == (0, 0.0)


@pytest.mark.parametrize("mode", ["correct", "incomplete_second_turn", "controlled_interrupt", "judge_unknown"])
def test_gate_c_project_summary_matches_official_judge(tmp_path, monkeypatch, mode):
    from security_adaptive_mas import run_framework as framework
    design = load_design(ROOT / "configs/research_v0_6.yaml")
    sys.path.insert(0, design["paths"]["are_root"])
    from security_adaptive_mas.adapters import are_modular
    design["paths"]["sandbox_root"] = str(tmp_path / "sandbox")
    fixture = C1Fixture(mode)
    def complete(**kwargs):
        if mode == "controlled_interrupt":
            raise KeyboardInterrupt("explicit free controlled-stop fixture")
        return fixture.complete(**kwargs)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "free-fixture-only")
    monkeypatch.setattr(framework, "DeepSeekClient", lambda budget, settings: DeepSeekClient(budget, settings, completion_fn=complete))
    monkeypatch.setattr(framework, "TraceLogger", lambda path, mode: TraceLogger(path, "offline_mock"))
    original_import = framework.import_benchmark_scenario
    def imported(*args, **kwargs):
        scenario = original_import(*args, **kwargs)
        fixture.scenario = scenario
        return scenario
    monkeypatch.setattr(framework, "import_benchmark_scenario", imported)
    original_builder = are_modular.make_modular_agent_builder
    def make_builder(session, client, **kwargs):
        fixture.session = session
        builder = original_builder(session, client, **kwargs)
        original_build = builder.build
        def build(*args, **kw):
            fixture.env = kw.get("env") or args[1]
            return original_build(*args, **kw)
        builder.build = build
        return builder
    monkeypatch.setattr(are_modular, "make_modular_agent_builder", make_builder)
    args = framework.build_parser().parse_args(["--scenario", "scenario_universe_22_0421h2", "--idle-wait-seconds", "5"])
    args.design = design
    from security_adaptive_mas.orchestration.configuration import design_hash
    args.design_hash = design_hash(design)
    watchdog = Timer(60, fixture.emergency_stop)
    watchdog.start()
    try:
        if mode == "controlled_interrupt":
            ledger = tmp_path / "shared/v0_1_cost_ledger.jsonl"
            ledger.parent.mkdir()
            original_reservation = '{"ledger_id":"historical_unsettled","cost_estimate_usd":0.4,"unknown_cost_bound":0}\n'
            ledger.write_text(original_reservation, encoding="utf-8")
            outcome = framework.main(["--scenario", args.scenario, "--idle-wait-seconds", "5",
                "--output-dir", str(ledger.parent), "--acknowledge-model-cost"], design_override=design)
            summary = outcome["summaries"][0]
            episode_base = Path(outcome["batch_dir"])
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            assert ledger.read_text(encoding="utf-8").startswith(original_reservation)
            assert [r.get("status") for r in rows[1:]] == ["reserved_before_episode", "settled"]
            assert rows[-1]["unknown_cost_bound"] == summary["unknown_cost_bound"] > 0
        else:
            summary = framework.run_case(args, project_config(design), args.scenario, tmp_path,
                                         framework.code_state())
            episode_base = tmp_path
    finally:
        watchdog.cancel()
    assert not fixture.watchdog_fired
    assert not fixture.env.thread.is_alive()
    out = episode_base / args.scenario / summary["run_id"]
    evaluation = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    assert summary["max_turns"] == 20 and summary["benchmark_runtime"]["native_turns"] == 2
    if mode in {"controlled_interrupt", "judge_unknown"}:
        assert summary["run_status"] == ("FAILED" if mode == "controlled_interrupt" else "BLOCKED_EVALUATION")
        assert summary["exception_type"] == ("KeyboardInterrupt" if mode == "controlled_interrupt" else "BenchmarkEvaluationBlocked")
        assert summary["legitimate_task_completed"] == "unknown"
        if mode == "controlled_interrupt":
            assert summary["official_runner_returned"] is False and summary["trace_paths"]["official"] is None
            assert summary["unknown_cost_bound"] > 0 and summary["model_requests_submitted"] == 1
        else:
            assert summary["official_runner_returned"] and summary["requests_by_purpose"]["benchmark_judge"] > 0
            assert summary["benchmark_judge_status"] == "BLOCKED_EVALUATION"
        assert (out / "failure.json").is_file()
    else:
        assert summary["run_status"] == "NORMAL_END"
        assert summary["benchmark_judge_completed"]
        assert summary["legitimate_task_completed"] is (mode == "correct")
        assert summary["official_runner_success"] is (mode == "correct")
        assert Path(summary["trace_paths"]["official"]).is_file()
    assert evaluation["legitimate_task_completed"] == summary["legitimate_task_completed"]
    save(tmp_path / "fixture_evidence.json", {"fixture_mode": mode, "paid_api_requests": 0, "summary": summary})


def test_gate_c_judge_uses_shared_budget_and_missing_judge_is_unknown(tmp_path, monkeypatch):
    from security_adaptive_mas.are_adapter import BudgetedJudgeEngine, benchmark_evaluation, BenchmarkEvaluationBlocked
    from security_adaptive_mas.deepseek import BudgetExceeded
    monkeypatch.setenv("DEEPSEEK_API_KEY", "free-fixture-only")
    client = DeepSeekClient(RunBudget(TraceLogger(tmp_path / "framework_trace.jsonl"), 2, 800000, .4),
                           completion_fn=lambda **kw: response("[[Success]]"))
    engine = BudgetedJudgeEngine(client)
    messages = [{"role": "user", "content": "Explicit offline Judge-call accounting fixture"}]
    engine(messages)
    client.complete(messages, purpose="coordinator")
    with pytest.raises(BudgetExceeded):
        engine(messages)
    assert client.budget.requests_by_purpose == {"benchmark_judge": 1, "coordinator": 1}
    assert client.budget.actual_usage_by_purpose["benchmark_judge"]["input_tokens"] == 10
    verdict = benchmark_evaluation(None, SimpleNamespace(success=True, exception=None))
    assert verdict["legitimate_task_completed"] == "unknown" and not verdict["benchmark_judge_completed"]
    with pytest.raises(BenchmarkEvaluationBlocked):
        import_benchmark_scenario(None, None, client=None)


@pytest.mark.parametrize("mode", ["correct", "missing_first_action", "wrong_first_argument"])
def test_gate_b_real_multiturn_and_dependency_rejection(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "free-fixture-only")
    fixture, result, events, client = run_fixture(tmp_path / mode, mode)
    from are.simulation.types import EventType
    delivered = [e for e in events if e.event_type == EventType.ENV]
    assert result.exception is None, str(result.exception)
    assert Path(result.export_path).is_file()
    assert client.budget.requests_by_purpose.get("benchmark_judge", 0) == 0
    if mode == "correct":
        assert result.success is True and fixture.session.turns == 2
        assert len(delivered) == 2
        reply = next(e for e in delivered if "4a046588" in e.event_id)
        send = next(e for e in events if e.event_id == "OracleEvent-AGENT-1649675d-005c-4313-88e2-721b08d7f4c4")
        assert reply.action.resolved_args["conversation_id"] == send.metadata.return_value
        assert any("November 7-10" in json.dumps(m, ensure_ascii=False) for m in fixture.reply_inputs)
        assert "OracleEvent-AGENT-" not in json.dumps(fixture.reply_inputs, ensure_ascii=False)
        # The future message must not appear before the real first turn ends.
        assert all("November 7-10" not in json.dumps(m, ensure_ascii=False) for m in fixture.reply_inputs[:3])
    else:
        assert result.success is False and fixture.session.turns == 1
        assert delivered == []
