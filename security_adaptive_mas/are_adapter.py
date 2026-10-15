"""Narrow ARE integration points; importing this module does not import ARE."""
from __future__ import annotations

import sys
import os
import tempfile
import time
from pathlib import Path
from threading import RLock
from types import MethodType

from .config import ROOT


def prepare_are_sandbox(*, filesystem=None, sandbox=None):
    """Keep ARE's temporary filesystem inside the D-drive project."""
    sandbox = ROOT / "work" / "are_tmp" if sandbox is None else Path(sandbox)
    filesystem = ROOT / "work" / "gaia2_filesystem" / "demo_filesystem" if filesystem is None else Path(filesystem)
    if not filesystem.is_dir():
        raise FileNotFoundError(f"Pinned Gaia2 filesystem missing: {filesystem}")
    sandbox.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = str(sandbox)
    os.environ["TEMP"] = str(sandbox)
    tempfile.tempdir = str(sandbox)
    os.environ["DEMO_FS_PATH"] = str(filesystem)


def import_official_scenario(record, are_root, *, filesystem=None, sandbox=None):
    """Use ARE's own JSON importer; never inspect future events in controller."""
    if str(are_root) not in sys.path:
        sys.path.insert(0, str(are_root))
    prepare_are_sandbox(filesystem=filesystem, sandbox=sandbox)
    from are.simulation.data_handler.importer import JsonScenarioImporter

    scenario, _completed, _logs = JsonScenarioImporter().import_from_json(
        record.path.read_bytes(), load_completed_events=False
    )
    if scenario.scenario_id != record.scenario_id:
        raise ValueError("Official importer scenario ID mismatch")
    scenario.initialize(sandbox_dir=str(sandbox or ROOT / "work" / "are_tmp"))
    return scenario


class BenchmarkEvaluationBlocked(RuntimeError):
    """A benchmark cannot be run without its evaluation contract."""


class BudgetedJudgeEngine:
    """Official Judge callable, sharing the episode client and budget."""

    def __init__(self, client):
        self.client = client

    def __call__(self, messages, **kwargs):
        response = self.client.complete(messages, purpose="benchmark_judge",
            instance_id="benchmark_judge", input_components=[{"kind": "harness_private_judge"}])
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise BenchmarkEvaluationBlocked("Benchmark Judge returned no text")
        return content, {"usage": response.usage}


def import_benchmark_scenario(record, are_root, *, client, max_turns=20,
                              filesystem=None, sandbox=None, on_error=None):
    """Prepare the pinned official benchmark; Oracle data stays in the Harness.

    Do not initialize the plain Scenario first. Official preprocessing owns the
    one initialization and the reference-only Oracle run/soft reset.
    """
    if client is None:
        raise BenchmarkEvaluationBlocked("A budgeted benchmark Judge client is required")
    if str(are_root) not in sys.path:
        sys.path.insert(0, str(are_root))
    prepare_are_sandbox(filesystem=filesystem, sandbox=sandbox)
    from are.simulation.data_handler.importer import JsonScenarioImporter
    from are.simulation.scenarios.scenario_imported_from_json.utils import preprocess_scenario
    from are.simulation.validation.configs import GraphPerEventJudgeConfig
    # Judge may run on ARE's environment thread while RiskMonitor is finishing
    # a tool result. Serialize the same client's requests; do not create a second
    # counter, change token limits, or bypass the original reservation logic.
    if not hasattr(client, "_benchmark_request_lock"):
        client._benchmark_request_lock = RLock()
        complete = client.complete
        def serialized(*args, **kwargs):
            with client._benchmark_request_lock:
                return complete(*args, **kwargs)
        client.complete = serialized

    def tracer(**metadata):
        def decorate(function):
            def observed(instance, *args, **kwargs):
                try:
                    result = function(instance, *args, **kwargs)
                except Exception as error:
                    client.budget.trace.log("benchmark_judge_error", exception_type=type(error).__name__, reason=str(error))
                    if on_error is not None:
                        on_error(error)
                    raise
                if result is None and str(metadata.get("trace_type", "")).endswith("_checker"):
                    # ARE's LLMChecker explicitly returns None when no verdict
                    # can be parsed. Keep its raw result, but do not publish it
                    # as a reliable negative task score.
                    client.benchmark_evaluation_error = BenchmarkEvaluationBlocked(
                        f"Official Judge returned unknown: {metadata['trace_type']}")
                    client.budget.trace.log("benchmark_judge_unknown", checker=metadata["trace_type"])
                if metadata.get("trace_type") == "judge" and hasattr(result, "success"):
                    client.budget.trace.log("benchmark_judgment", turn_index=instance.state.turn_idx,
                        success=result.success, reason=str(result.failure),
                        event_id_mapping=result.agent_event_id_to_oracle_event_id)
                return result
            return observed
        return decorate

    scenario, _, _ = JsonScenarioImporter().import_from_json_to_benchmark(
        record.path.read_bytes(), load_completed_events=False)
    if scenario.scenario_id != record.scenario_id:
        raise ValueError("Official benchmark importer scenario ID mismatch")
    judge_config = GraphPerEventJudgeConfig(engine=BudgetedJudgeEngine(client), tracer=tracer)
    preprocess_scenario(scenario, judge_config=judge_config)
    if not getattr(scenario, "judge", None) or not scenario._turns_initialized:
        raise BenchmarkEvaluationBlocked("Official preprocessing did not install the benchmark Judge")
    if scenario.nb_turns is None or not 1 <= scenario.nb_turns <= max_turns:
        raise BenchmarkEvaluationBlocked(f"Native benchmark turns {scenario.nb_turns} exceed execution cap {max_turns}")
    scenario.benchmark_runtime_metadata = {
        "importer": "import_from_json_to_benchmark", "preprocessor": "preprocess_scenario",
        "scenario_type": type(scenario).__name__, "preprocessed": True,
        "judge": type(scenario.judge).__name__, "judge_mode": "official_default_hard_and_soft",
        "judge_model": "deepseek-flash", "judge_budget": "shared_episode",
        "judge_configured": True, "native_turns": scenario.nb_turns, "execution_turn_cap": max_turns,
        "runtime_duration": scenario.duration, "reference_oracle_run": "harness_only_soft_reset_before_agent",
        "scenario_sha256": record.sha256}
    client.budget.trace.log("benchmark_preprocessed", **scenario.benchmark_runtime_metadata)
    return scenario


class BenchmarkLifecycle:
    """Observe the official loop and optionally bound idle wall time, not API time."""

    def __init__(self, trace, client, idle_wait_seconds=None):
        if idle_wait_seconds is not None and idle_wait_seconds <= 0:
            raise ValueError("idle_wait_seconds must be positive")
        self.trace, self.client = trace, client
        self.idle_wait_seconds = idle_wait_seconds
        self.env = self.agent = self.session = None
        self.stop_reason = self.stop_snapshot = None
        self.idle_since = None

    def snapshot(self):
        if self.env is None:
            return None
        env = self.env
        past = env.event_log.list_view()
        completed = {e.event_id for e in past}
        return {"environment_state": env.state.value, "simulation_time": env.time_manager.time(),
            "simulation_elapsed_seconds": env.time_manager.time_passed(),
            "simulation_paused": env.time_manager.is_paused, "tick_count": env.tick_count,
            "agent_running_state": str(self.agent.react_agent.custom_state.get("running_state")),
            "actual_agent_turns": self.session.turns,
            "last_completed_event": past[-1].event_id if past else None,
            "message_queue": [{"type": m.message_type.value, "timestamp": str(m.timestamp)}
                              for m in env.notification_system.message_queue.list_view()],
            "queued_event_ids": [e.event_id for e in env.event_queue.list_view()],
            "uncompleted_dependencies": [{"event_id": e.event_id,
                "dependencies": [d.event_id for d in e.dependencies if d.event_id not in completed]}
                for e in env.events_to_schedule if e.event_id not in completed]}

    def request_stop(self, reason):
        self.stop_reason, self.stop_snapshot = reason, self.snapshot()
        self.trace.log("benchmark_lifecycle_stop", reason=reason, snapshot=self.stop_snapshot)
        if self.env is not None:
            self.env.stop()

    def bind(self, agent, env, session):
        from are.simulation.agents.default_agent.base_agent import RunningState
        self.agent, self.env, self.session = agent, env, session
        original = agent.get_notifications
        def get_notifications(instance, notification_system):
            messages = original(notification_system)
            if any(messages):
                self.idle_since = None
            elif (agent.react_agent.custom_state.get("running_state") == RunningState.TERMINATED
                  and not env.time_manager.is_paused and self.client.budget.request_reservation == (0, 0.0)):
                now = time.monotonic()
                if self.idle_since is None:
                    self.idle_since = now
                    self.trace.log("benchmark_idle_started", snapshot=self.snapshot())
                elif self.idle_wait_seconds is not None and now - self.idle_since >= self.idle_wait_seconds:
                    self.request_stop("INDETERMINATE_IDLE")
                    return original(notification_system)
            else:
                self.idle_since = None
            return messages
        agent.get_notifications = MethodType(get_notifications, agent)

    def close(self):
        if self.env is not None:
            self.env.stop()
            self.env.thread.join(timeout=5)
            self.trace.log("benchmark_environment_cleanup", thread_alive=self.env.thread.is_alive())


def benchmark_evaluation(scenario, result, *, interrupted=False):
    """Separate a returned Runner result from an evaluated benchmark task."""
    judge = getattr(scenario, "judge", None)
    evaluated = bool(judge and judge.state.initialized and judge.state.turn_idx >= 0
                     and (judge.state.turn_idx == judge.state.nb_turns - 1 or not judge.state.last_turn_success)
                     and result is not None and type(result.success) is bool
                     and result.exception is None and not interrupted)
    return {"official_runner_returned": result is not None,
        "benchmark_judge_status": "VALID" if evaluated else "BLOCKED_EVALUATION",
        "benchmark_judge_completed": evaluated,
        "benchmark_judge_turn_index": judge.state.turn_idx if judge else None,
        "legitimate_task_completed": bool(result.success) if evaluated else "unknown",
        "semantic_verified": "official_benchmark_judge" if evaluated else "unknown"}


def official_runner(agent_builder=None):
    """Construct ARE runner with its validator and trace export intact."""
    from are.simulation.scenario_runner import ScenarioRunner

    return ScenarioRunner(agent_builder=agent_builder)


def inspect_tool_metadata(scenario):
    """Return ARE app tool metadata; unknown write status remains unsafe."""
    return [
        {"name": tool._public_name or tool.name, "app": tool.app_name,
         "write_operation": tool.write_operation}
        for tool in scenario.get_tools()
    ]
