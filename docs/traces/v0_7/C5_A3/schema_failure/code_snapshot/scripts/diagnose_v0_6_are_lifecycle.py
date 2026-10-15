"""Free diagnosis only: reuse saved responses through the unchanged ARE adapter.

The observation boundary below is an instrumented diagnostic stop, NOT a
production timeout, task completion rule, or paid experiment runner.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from types import MethodType, SimpleNamespace

from security_adaptive_mas.config import ROOT
from security_adaptive_mas.are_adapter import import_official_scenario, official_runner
from security_adaptive_mas.deepseek import DeepSeekClient, RunBudget
from security_adaptive_mas.orchestration.configuration import load_design, project_config
from security_adaptive_mas.orchestration.session import ModularSession
from security_adaptive_mas.run_framework import code_state, ledger_charge, save
from security_adaptive_mas.trace import TraceLogger


def runtime_snapshot(env, agent, session):
    """Read state for offline diagnosis; never pass it to an LLM or Controller."""
    past = env.event_log.list_view()
    done = {e.event_id for e in past}
    def event_row(e):
        return {"event_id": e.event_id, "class_name": type(e).__name__,
                "event_type": e.event_type.value, "event_time": e.event_time,
                "dependencies": [{"event_id": d.event_id, "event_time": d.event_time,
                                   "present_in_completed_log": d.event_id in done}
                                  for d in e.dependencies]}
    messages = env.notification_system.message_queue.list_view()
    return {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "environment_state": env.state.value,
            "environment_thread_alive": env.thread.is_alive(),
            "agent_running_state": str(agent.react_agent.custom_state.get("running_state")),
            "configured_max_turns": agent.max_turns, "completed_turns": session.turns,
            "duration": env.duration, "simulation_time": env.time_manager.time(),
            "simulation_elapsed_seconds": env.time_manager.time_passed(),
            "time_manager_paused": env.time_manager.is_paused,
            "pause_event_set": env.pause_event.is_set(), "tick_count": env.tick_count,
            "next_event_time": env.get_next_event_time(),
            "message_queue": [{"type": m.message_type.value, "timestamp": str(m.timestamp)} for m in messages],
            "pending_event_queue": [event_row(e) for e in env.event_queue.list_view()],
            "declared_uncompleted_events": [event_row(e) for e in env.events_to_schedule if e.event_id not in done],
            "completed_events": [e.to_dict() for e in past],
            "last_completed_event_time": max((e.event_time for e in past if e.event_time is not None), default=None)}


def diagnose(original_run, output, *, observe_idle_seconds=3.0, preserve_latency=True, ledger=None):
    original_run, output = Path(original_run).resolve(), Path(output).resolve()
    if observe_idle_seconds <= 0:
        raise ValueError("positive_diagnostic_observation_boundary_required")
    output.mkdir(parents=True, exist_ok=False)
    old_config = json.loads((original_run / "run_config.json").read_text(encoding="utf-8"))
    old_events = [json.loads(line) for line in (original_run / "framework_trace.jsonl").read_text(encoding="utf-8").splitlines()]
    received = {e["request_id"]: e for e in old_events if e["kind"] == "model_response_received"}
    fixtures, inputs = [], [original_run / "run_config.json", original_run / "framework_trace.jsonl", original_run / "runner.log"]
    for event in old_events:
        if event["kind"] != "model_request_submitted":
            continue
        request_id = event["request_id"]
        prepared, attempt = request_id.split(":")
        request_path = original_run / "model_io" / (prepared + "_request.json")
        response_path = original_run / "model_io" / (prepared + "_" + attempt + "_response.json")
        fixtures.append((event["purpose"], json.loads(response_path.read_text(encoding="utf-8")), received[request_id]["elapsed_seconds"]))
        inputs.extend([request_path, response_path])
    design = load_design(ROOT / "configs/research_v0_6.yaml")
    if design["method"] != old_config["organization_policy"]:
        raise ValueError("historical_method_mismatch")
    config = project_config(design)
    # Only cached model outputs are supplied. No scenario future/Oracle inputs.
    called = []
    def complete(**kwargs):
        index = len(called)
        if index >= len(fixtures):
            raise RuntimeError("diagnostic_cached_responses_exhausted")
        purpose, response, elapsed = fixtures[index]
        actual = "risk_assessment" if kwargs.get("response_format") == {"type": "json_object"} else "coordinator"
        if purpose != actual:
            raise RuntimeError("diagnostic_response_purpose_mismatch")
        called.append(purpose)
        if preserve_latency:
            time.sleep(elapsed)
        return SimpleNamespace(usage=SimpleNamespace(**response["usage"]),
                               choices=[SimpleNamespace(message=SimpleNamespace(**c["message"])) for c in response["choices"]])

    trace = TraceLogger(output / "framework_trace.jsonl", mode="offline_mock")
    client = DeepSeekClient(RunBudget(trace, old_config["max_requests"], old_config["max_total_tokens"], old_config["max_cost_usd"]), completion_fn=complete)
    session = ModularSession(design, trace, client)
    scenario = import_official_scenario(config.select(old_config["scenario_id"]), config.are_root,
                                       filesystem=design["paths"]["gaia2_filesystem"], sandbox=design["paths"]["sandbox_root"])
    from security_adaptive_mas.adapters.are_modular import make_modular_agent_builder
    from are.simulation.agents.default_agent.base_agent import RunningState
    from are.simulation.scenarios.config import ScenarioRunnerConfig
    builder = make_modular_agent_builder(session, client)
    build = builder.build
    snapshots, captured = [], {}
    def diagnostic_build(instance, agent_config, env=None, mock_responses=None):
        agent = build(agent_config, env, mock_responses)
        captured.update(env=env, agent=agent)
        pull = agent.get_notifications
        idle_since = None
        def observe(instance, notification_system):
            nonlocal idle_since
            messages = pull(notification_system)
            if any(messages):
                idle_since = None
            elif agent.react_agent.custom_state.get("running_state") == RunningState.TERMINATED:
                now = time.monotonic()
                if idle_since is None:
                    idle_since = now
                    snapshots.append(runtime_snapshot(env, agent, session))
                elif now - idle_since >= observe_idle_seconds:
                    snapshots.append(runtime_snapshot(env, agent, session))
                    save(output / "runtime_snapshots.json", snapshots)
                    trace.log("diagnostic_stop_requested", reason="offline_observation_boundary_not_task_completion", wall_idle_seconds=now-idle_since)
                    env.stop()
                    return pull(notification_system)
            return messages
        agent.get_notifications = MethodType(observe, agent)
        return agent
    builder.build = MethodType(diagnostic_build, builder)
    runner_config = ScenarioRunnerConfig(agent="default", model="deepseek-flash", model_provider="deepseek", endpoint="https://api.deepseek.com",
                                        oracle=False, export=True, max_turns=20, output_dir=str(output / "official_trace"))
    before = Path(ledger).read_bytes() if ledger is not None else None
    old_key = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = "offline-diagnostic-fixture"
    started = time.monotonic()
    handler = logging.FileHandler(output / "runner.log", encoding="utf-8")
    logging.getLogger().addHandler(handler)
    try:
        with (output / "runner.log").open("a", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
            result = official_runner(builder).run(runner_config, scenario)
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        if "env" in captured:
            # Diagnostic cleanup only, including a failed assertion/model fixture.
            captured["env"].resume()
            captured["env"].stop()
            captured["env"].thread.join(timeout=5)
        if old_key is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = old_key
    report = {"kind": "offline_lifecycle_diagnosis_not_paid_episode", "original_run": str(original_run),
              "original_run_id": old_config["run_id"], "original_code_revision": old_config["code_revision"],
              "diagnostic_source": code_state(), "scenario_hash": config.select(scenario.scenario_id).sha256,
              "scenario_class": type(scenario).__name__, "runtime_duration": scenario.duration,
              "scenario_nb_turns": scenario.nb_turns, "configured_max_turns": 20,
              "stop_reason": "diagnostic_observation_boundary", "task_completed": "unknown",
              "official_runner_returned": True, "official_runner_raw_success": result.success,
              "official_exception": str(result.exception) if result.exception else None,
              "official_export_path": str(result.export_path),
              "validation_interpretation": "plain_Scenario.validate_checks_environment_state_only_not_Gaia2_goal",
              "cached_response_count": len(called), "cached_purposes": called,
              "preserved_historical_response_latency": preserve_latency, "new_paid_api_requests": 0, "new_paid_cost_usd": 0,
              "cached_usage_is_new_api_usage": False, "diagnostic_wall_time_seconds": time.monotonic()-started,
              "worker_steps": session.worker_steps, "graph_patches": session.runtime.applied,
              "original_input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
    if ledger is not None:
        report.update(ledger=str(ledger), ledger_unchanged=Path(ledger).read_bytes()==before,
                      ledger_charge=ledger_charge(Path(ledger)), ledger_sha256=hashlib.sha256(before).hexdigest())
    save(output / "diagnosis.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--observe-idle-seconds", type=float, default=3.0)
    parser.add_argument("--without-historical-latency", action="store_true")
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args()
    report = diagnose(args.original_run_dir, args.output_dir, observe_idle_seconds=args.observe_idle_seconds,
                      preserve_latency=not args.without_historical_latency, ledger=args.ledger)
    print(json.dumps({k: report[k] for k in ("kind", "cached_response_count", "runtime_duration", "official_runner_returned", "official_runner_raw_success", "new_paid_api_requests")}, ensure_ascii=False))
