"""Pin the diagnosed integration problem; these are NOT lifecycle-fix acceptance tests."""
import json
import time

from security_adaptive_mas.are_adapter import import_official_scenario
from security_adaptive_mas.config import ROOT
from security_adaptive_mas.orchestration.configuration import load_design, project_config


def load_c1(tmp_path):
    design = load_design(ROOT / "configs/research_v0_6.yaml")
    config = project_config(design)
    record = config.select("scenario_universe_22_0421h2")
    scenario = import_official_scenario(record, config.are_root,
        filesystem=design["paths"]["gaia2_filesystem"], sandbox=tmp_path)
    return scenario, json.loads(record.path.read_text(encoding="utf-8"))


def test_actual_importer_keeps_default_duration_and_unbound_oracle_dependencies(tmp_path):
    scenario, raw = load_c1(tmp_path)
    from are.simulation.scenarios.scenario import Scenario
    from are.simulation.types import OracleEvent, EventType
    assert raw["metadata"]["definition"]["duration"] is None
    assert type(scenario).__name__ == "ScenarioImportedFromJson"
    assert scenario.duration == 1000 and scenario.nb_turns is None
    # No benchmark goal judge is installed on this actual project import path.
    assert scenario.validate.__func__ is Scenario.validate
    future = [e for e in scenario.events if e.event_type == EventType.ENV]
    assert len(future) == 2
    assert all(len(e.dependencies) == 1 and isinstance(e.dependencies[0], OracleEvent) for e in future)
    assert {e.dependencies[0].event_id for e in future} == {
        "OracleEvent-AGENT-68ee35ca-d7bb-4135-a7fb-9c5adf14bde7"}


def test_real_non_oracle_environment_drops_oracle_successor_schedule(tmp_path):
    scenario, _ = load_c1(tmp_path)
    from are.simulation.environment import Environment, EnvironmentConfig
    from are.simulation.notification_system import VerboseNotificationSystem
    from are.simulation.types import EnvironmentState, EventType
    env = Environment(config=EnvironmentConfig(oracle_mode=False, queue_based_loop=False,
        start_time=scenario.start_time, exit_when_no_events=False),
        notification_system=VerboseNotificationSystem())
    try:
        env.run(scenario, wait_for_end=False)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if env.time_manager.time_passed() >= 2 and not env.event_queue.list_view():
                break
            time.sleep(.1)
        assert env.state == EnvironmentState.RUNNING
        assert not env.pause_event.is_set() and not env.time_manager.is_paused
        assert env.event_queue.list_view() == []
        completed = env.event_log.list_view()
        assert any(e.event_type == EventType.USER for e in completed)
        assert not any(e.event_type == EventType.ENV for e in completed)
        assert all(e.dependencies[0].event_time is None
                   for e in scenario.events if e.event_type == EventType.ENV)
    finally:
        env.resume()
        env.stop()
        env.thread.join(timeout=5)
    assert not env.thread.is_alive()
