from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import sys
from uuid import uuid4

import pytest

from security_adaptive_mas.are_bridge import (ObservedContext, make_guarded_agent_builder,
                                               record_are_delivery)
from security_adaptive_mas.config import ROOT, load_config
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.trace import TraceLogger
from security_adaptive_mas.online_observation import Knowledge, OnlineObservationRecorder
from security_adaptive_mas.online_task import (Certainty, OnlineTaskAnalyzer,
    Subtask, TaskDependency, TaskStatus, TaskUpdate)
from security_adaptive_mas.topology_candidates import (AgentPlan, BaselinePerformanceEvaluator,
    MantaCandidateGenerator, TaskAssignment, TemplateCandidateGenerator,
    TopologyCandidate, TopologySelector, TopologyValidator, candidate_from_manta)


def make_controller():
    trace = TraceLogger(ROOT / "work" / "test_logs" / f"{uuid4().hex}.jsonl")
    return Controller(load_config(), trace, frozenset({"read"}), frozenset({"Files"}))


def delivered(controller, context, users=(), environment=()):
    record_are_delivery(context, [SimpleNamespace(message=text) for text in users],
                        [SimpleNamespace(message=text) for text in environment])
    return controller.online_snapshot()


def test_future_event_change_cannot_change_current_observation():
    def snapshot_with_future(future_text):
        config = load_config()
        if str(config.are_root) not in sys.path:
            sys.path.insert(0, str(config.are_root))
        from are.simulation.notification_system import Message, MessageQueue, MessageType

        controller = make_controller()
        seed = controller.provenance.add("startup", "low_trust", "waiting")
        context = ObservedContext(controller, seed.evidence_id)
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        queue = MessageQueue()
        queue.put(Message(MessageType.USER_MESSAGE, "Investigate task A", now))
        future = Message(MessageType.ENVIRONMENT_NOTIFICATION, future_text,
                         now + timedelta(hours=1))
        queue.put(future)
        arrived = queue.get_by_timestamp(now)
        record_are_delivery(context, arrived, ())
        snapshot = controller.online_snapshot()
        future.message = "changed again"
        assert queue.get_by_timestamp(now) == []
        assert queue.get_by_timestamp(now + timedelta(hours=1)) == [future]
        return snapshot

    first = snapshot_with_future("ENV event tomorrow")
    second = snapshot_with_future("completely different future")
    assert first == second
    assert first.task_description == "Investigate task A"
    assert first.work.knowledge == Knowledge.UNKNOWN
    assert first.budget.knowledge == Knowledge.UNKNOWN
    assert first.topology_version is None


def test_oracle_and_judge_have_no_online_access_path():
    controller = make_controller()
    seed = controller.provenance.add("startup", "low_trust", "waiting")
    context = ObservedContext(controller, seed.evidence_id)
    snapshot = delivered(controller, context, users=("A visible task",),
                         environment=("A new legal event",))
    analysis = controller.online_analyzer.analyze(snapshot)
    candidates = controller.candidate_generator.generate(analysis)
    assert not hasattr(snapshot, "scenario")
    assert not hasattr(snapshot, "oracle_dag")
    assert not hasattr(snapshot, "judge_result")
    assert "oracle" not in repr((snapshot, analysis, candidates)).lower()
    assert all(set(candidate.evidence_ids) <= set(snapshot.evidence_ids) for candidate in candidates)


def test_are_builder_records_only_notifications_returned_by_runtime(monkeypatch):
    config = load_config()
    if str(config.are_root) not in sys.path:
        sys.path.insert(0, str(config.are_root))
    from are.simulation.agents.agent_builder import AgentBuilder

    class FakeAgent:
        def get_notifications(self, notification_system):
            return ([SimpleNamespace(message="delivered task")],
                    [SimpleNamespace(message="delivered env")], [])

        def init_tools(self, scenario):
            pass

    monkeypatch.setattr(AgentBuilder, "build", lambda self, agent_config, env=None,
                        mock_responses=None: FakeAgent())
    controller = make_controller()
    seed = controller.provenance.add("startup", "low_trust", "waiting")
    context = ObservedContext(controller, seed.evidence_id)
    builder = make_guarded_agent_builder(controller, context)
    agent = builder.build(None)
    users, environment, _ = agent.get_notifications(None)
    assert users[0].message == "delivered task"
    assert environment[0].message == "delivered env"
    snapshot = controller.online_snapshot()
    assert [event.kind for event in snapshot.events] == ["user", "environment"]


def test_new_delivered_event_can_update_d_t_with_explicit_evidence():
    controller = make_controller()
    seed = controller.provenance.add("startup", "low_trust", "waiting")
    context = ObservedContext(controller, seed.evidence_id)
    before = delivered(controller, context, users=("Review file A",))
    d1 = OnlineTaskAnalyzer().analyze(before)
    after = delivered(controller, context, environment=("File B has changed",))
    d2 = OnlineTaskAnalyzer().analyze(after)
    assert len(d1.subtasks) == 1
    assert len(d2.subtasks) == 1
    assert d2.unresolved_event_ids == (after.environment_events[0].evidence_id,)
    update = TaskUpdate(subtasks=(Subtask("review_b", "Review changed file B",
                                          TaskStatus.PENDING, d2.unresolved_event_ids),),
                        dependencies=(TaskDependency("root", "review_b",
                                                     Certainty.INFERRED, d2.unresolved_event_ids),))
    controller.add_task_update(update)
    d3, _, _ = controller.online_candidates()
    assert len(d3.subtasks) == 2
    assert len(d3.dependencies) == 1
    assert d3.unresolved_event_ids == ()
    assert d2.as_of_sequence == d1.as_of_sequence + 1
    assert d3.parallelism is None
    with pytest.raises(ValueError, match="unseen evidence"):
        controller.add_task_update(TaskUpdate(subtasks=(
            Subtask("future", "Unseen future event", TaskStatus.PENDING, ("oracle_future",)),)))
    assert len(controller.task_updates) == 1


def test_task_graph_rejects_unseen_evidence_and_cycle():
    recorder = OnlineObservationRecorder()
    recorder.record_notification("user", "A", "ev_1")
    recorder.record_notification("environment", "B", "ev_2")
    observation = recorder.snapshot()
    update = TaskUpdate(subtasks=(Subtask("second", "B", TaskStatus.PENDING, ("ev_2",)),))
    analysis = OnlineTaskAnalyzer().analyze(observation, (update,))
    with pytest.raises(ValueError, match="unseen evidence"):
        replace(analysis, evidence_ids=("oracle_ref",)).validate(observation)
    cycle = replace(analysis, dependencies=(
        TaskDependency("root", "second", Certainty.CONFIRMED, ("ev_1",)),
        TaskDependency("second", "root", Certainty.INFERRED, ("ev_2",)),
    ))
    with pytest.raises(ValueError, match="cycle"):
        cycle.validate(observation)


def test_structured_candidates_and_validation():
    recorder = OnlineObservationRecorder()
    recorder.record_notification("user", "A", "ev_1")
    recorder.record_notification("environment", "B", "ev_2")
    analysis = OnlineTaskAnalyzer().analyze(recorder.snapshot(), (
        TaskUpdate(subtasks=(Subtask("second", "B", TaskStatus.PENDING, ("ev_2",)),)),))
    candidates = TemplateCandidateGenerator(3).generate(analysis)
    validator = TopologyValidator(3, frozenset({"read"}))
    assert {candidate.template for candidate in candidates} == {
        "singleton", "star", "chain", "fully_connected", "tree"}
    for candidate in candidates:
        validator.validate(candidate, analysis)
        assert candidate.runtime_support == "planning_only"
        assert len(candidate.assignments) == 2
    star = next(candidate for candidate in candidates if candidate.template == "star")
    with pytest.raises(ValueError, match="communication edge"):
        validator.validate(replace(star, edges=star.edges + (("unknown_agent", "agent_0"),)), analysis)
    with pytest.raises(ValueError, match="task assignment"):
        validator.validate(replace(star, assignments=(TaskAssignment("oracle_task", "agent_0"),)), analysis)
    with pytest.raises(ValueError, match="Unsupported execution protocol"):
        validator.validate(replace(star, protocol="debate"), analysis)
    with pytest.raises(ValueError, match="tool authorization"):
        validator.validate(replace(star, agents=(AgentPlan("agent_0", "coordinator", ("write",)),)), analysis)
    with pytest.raises(ValueError, match="Runtime cannot execute"):
        validator.validate(replace(star, runtime_support="are_dynamic_read_only"), analysis)


def test_manta_adapter_is_structural_and_selector_does_not_fabricate_benefit():
    controller = make_controller()
    recorder = controller.online_observations
    recorder.record_notification("user", "A", "ev_1")
    analysis = controller.online_analyzer.analyze(controller.online_snapshot())
    spec, _ = controller.planner.make("singleton", 1, frozenset({"read"}), ("A",))
    adapted = candidate_from_manta(spec, (TaskAssignment("root", "agent_0"),), analysis)
    assert adapted.runtime_support == "controller_spec_only"
    generated = MantaCandidateGenerator(controller.planner, frozenset({"read"})).generate(analysis)
    assert {item.template for item in generated} == {"singleton", "star", "chain"}
    validator = TopologyValidator(3, frozenset({"read"}),
                                  allowed_runtime_support=frozenset({"controller_spec_only"}))
    for candidate in generated:
        validator.validate(candidate, analysis)
    estimates = tuple(BaselinePerformanceEvaluator().evaluate(candidate, analysis)
                      for candidate in generated)
    selector = TopologySelector()
    assert selector.select(generated, estimates) is None
    assert selector.select(generated, estimates, generated[0].candidate_id) == generated[0]


def test_inspected_five_are_development_not_held_out():
    split = json.loads((ROOT / "configs" / "evaluation_split.json").read_text(encoding="utf-8"))
    assert set(split["development_scenarios"]) == set(load_config().scenario_ids)
    assert split["held_out_evaluation_scenarios"] == []
