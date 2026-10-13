import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from security_adaptive_mas.are_bridge import ObservedContext, guard_are_toolset
from security_adaptive_mas.config import load_config
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.deepseek import DeepSeekClient, DeepSeekSettings, RunBudget
from security_adaptive_mas.online_observation import ObservedEvent, OnlineObservationRecorder
from security_adaptive_mas.online_task import (Certainty, InformationRequirement, OnlineTaskAnalyzer,
                                             Subtask, TaskDependency, TaskStatus, TaskUpdate)
from security_adaptive_mas.performance_monitor import PerformanceMonitor, TriggerAction
from security_adaptive_mas.task_proposer import DeepSeekTaskProposer, TaskProposalError
from security_adaptive_mas.trace import TraceLogger


def make_controller(tmp_path, **options):
    return Controller(load_config(), TraceLogger(tmp_path / "trace.jsonl"),
                      frozenset(), frozenset(), **options)


def response(content):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
                           choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def proposal(source_ids, *, task_id="inspect", description="Inspect the requested result"):
    return {"source_evidence_ids": list(source_ids), "subtasks": [{
        "task_id": task_id, "description": description, "suggested_status": "completed",
        "evidence_ids": list(source_ids), "certainty": "confirmed"}],
        "dependencies": [], "information_requirements": []}


def client_for(trace, completion):
    return DeepSeekClient(RunBudget(trace, max_requests=10, max_total_tokens=100000, max_cost_usd=1),
                          DeepSeekSettings(max_output_tokens=128, max_attempts=1),
                          completion_fn=completion)


def test_recorder_compatible_positions_stable_ids_and_frozen_snapshot():
    assert ObservedEvent(1, "user", "ev_1", "task").event_id is None
    recorder = OnlineObservationRecorder()
    first = recorder.record_notification("user", "task", "ev_1", event_id="delivery_1",
                                          agent_instance_id="agent_0", status="observed",
                                          simulation_time=10.0)
    before = recorder.snapshot()
    assert recorder.record_notification("user", "task", "ev_1", event_id="delivery_1",
                                        agent_instance_id="agent_0", status="observed",
                                        simulation_time=10.0) is first
    result = recorder.record_tool_result("read", "ev_2", status="completed")
    assert (first.sequence, result.sequence) == (1, 2)
    assert before.events == (first,) and recorder.snapshot().task_description == "task"
    assert result.call_id is None and result.elapsed_seconds is None


def test_prefilter_delivered_events_and_ordinary_result_do_not_call_model(tmp_path):
    controller = make_controller(tmp_path, monitor_enabled=True)
    recorder = controller.online_observations
    user = recorder.record_notification("user", "work", "ev_1")
    env = recorder.record_notification("environment", "no keyword needed", "ev_2")
    ordinary = recorder.record_tool_result("read", "ev_3", status="completed")
    assert controller.on_observed_event(user).action == TriggerAction.ANALYZE
    assert controller.on_observed_event(env).action == TriggerAction.ANALYZE
    assert controller.on_observed_event(ordinary).action == TriggerAction.KEEP
    assert controller.on_observed_event(ordinary).reasons == ("tool_result_unlinked",)
    controller.on_observed_event(user)
    assert len(controller.pending_analysis) == 2
    assert controller.process_pending_analysis().reasons == ("analysis_deferred_disabled",)
    assert controller.analysis_requests == 0 and controller.run_budget is None


def test_task_upsert_preserves_actual_status_and_protects_user_root():
    recorder = OnlineObservationRecorder()
    recorder.record_notification("user", "first request", "ev_1")
    analyzer = OnlineTaskAnalyzer()
    first = analyzer.analyze(recorder.snapshot())
    first = analyzer.apply_proposal(first, TaskUpdate(subtasks=(
        Subtask("child", "Original work", TaskStatus.PENDING, ("ev_1",)),)), recorder.snapshot())
    first = replace(first, subtasks=(replace(first.subtasks[0], status=TaskStatus.COMPLETED),
                                     replace(first.subtasks[1], status=TaskStatus.RUNNING)))
    recorder.record_notification("environment", "changed requirement", "ev_2")
    snapshot = recorder.snapshot()
    for description, evidence in (("Changed root from ENV", "ev_2"), ("first request", "ev_1")):
        with pytest.raises(ValueError, match="root_requires_delivered_user_notification"):
            analyzer.apply_proposal(first, TaskUpdate(subtasks=(
                Subtask("root", description, TaskStatus.COMPLETED, (evidence,)),)), snapshot)
    assert first.subtasks[0].description == "first request"
    assert first.subtasks[0].actual_status == TaskStatus.COMPLETED
    update = TaskUpdate(subtasks=(Subtask("child", "Updated work", TaskStatus.COMPLETED, ("ev_2",)),))
    second = analyzer.apply_proposal(first, update, snapshot)
    repeated = analyzer.apply_proposal(second, update, snapshot)
    assert repeated == second and len(second.subtasks) == 2
    child = second.subtasks[1]
    assert child.actual_status == TaskStatus.RUNNING
    assert child.suggested_status == TaskStatus.COMPLETED
    assert child.evidence_ids == ("ev_1", "ev_2")
    recorder.record_notification("user", "additional request", "ev_3")
    refreshed = analyzer.refresh(second, recorder.snapshot())
    assert sum(task.task_id == "root" for task in refreshed.subtasks) == 1
    assert refreshed.subtasks[0].description == "first request\nadditional request"
    assert refreshed.subtasks[0].evidence_ids == ("ev_1", "ev_3")
    assert refreshed.subtasks[0].actual_status == TaskStatus.COMPLETED


def test_proposal_evidence_dependency_upsert_and_removal():
    recorder = OnlineObservationRecorder()
    recorder.record_notification("user", "work", "ev_1")
    snapshot = recorder.snapshot()
    analyzer = OnlineTaskAnalyzer()
    first = analyzer.analyze(snapshot)
    update = TaskUpdate((Subtask("child", "child", TaskStatus.COMPLETED, ("ev_1",)),),
                        (TaskDependency("root", "child", Certainty.CONFIRMED, ("ev_1",)),))
    second = analyzer.apply_proposal(first, update, snapshot)
    assert second.subtasks[1].actual_status == TaskStatus.PENDING
    assert second.subtasks[1].suggested_status == TaskStatus.COMPLETED
    assert second.dependencies[0].certainty == Certainty.INFERRED
    assert len(analyzer.apply_proposal(second, update, snapshot).dependencies) == 1
    with pytest.raises(ValueError, match="unseen evidence"):
        analyzer.apply_proposal(second, TaskUpdate(source_evidence_ids=("future",)), snapshot)
    with pytest.raises(ValueError, match="cycle"):
        analyzer.apply_proposal(second, TaskUpdate(dependencies=(
            TaskDependency("child", "root", Certainty.INFERRED, ("ev_1",)),)), snapshot)
    removed = analyzer.apply_proposal(second, TaskUpdate(source_evidence_ids=("ev_1",),
                                                        remove_dependencies=(("root", "child"),)), snapshot)
    assert removed.dependencies == ()


def test_signals_unknown_units_and_explicit_information_result_link():
    recorder = OnlineObservationRecorder()
    recorder.record_notification("user", "work", "ev_1")
    analyzer, monitor = OnlineTaskAnalyzer(), PerformanceMonitor()
    first = analyzer.analyze(recorder.snapshot())
    unknown = monitor.signals(recorder.snapshot(), first, runtime_agent_instances=("agent_0",))
    assert unknown.parallel_opportunity.value is None and unknown.parallel_opportunity.status == "unknown"
    assert unknown.ready_work_count.value is None and unknown.ready_work_count.unit == "work_items"
    assert unknown.context_redundancy.value is None
    assert unknown.coordination_waste.status == "not_applicable"
    assert monitor.signals(recorder.snapshot(), first).coordination_waste.status == "unknown"
    recorder.record_model_input("ev_2", "same-payload", 23, agent_instance_id="agent_0")
    recorder.record_model_input("ev_3", "same-payload", 23, agent_instance_id="agent_0")
    result = recorder.record_tool_result("read", "ev_4", status="completed")
    current = analyzer.apply_proposal(first, TaskUpdate(information_requirements=(
        InformationRequirement("root", "Read result", Certainty.INFERRED, ("ev_1",), "need_1"),)),
        recorder.snapshot())
    signals = monitor.signals(recorder.snapshot(), current)
    assert signals.context_redundancy.value == 23
    assert signals.context_redundancy.unit == "repeated_request_characters"
    assert signals.information_needs[0].provider_task_id is None
    decision = monitor.prefilter(result, recorder.snapshot(), current,
                                 related_requirements=(("root", "need_1"),))
    assert decision.action == TriggerAction.ANALYZE


def test_task_proposer_uses_injected_completion_visible_excerpts_and_shared_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    recorder = OnlineObservationRecorder()
    recorder.record_notification("user", "work", "ev_1")
    recorder.record_tool_result("read", "ev_2", status="completed")
    snapshot = recorder.snapshot()
    trace = TraceLogger(tmp_path / "usage.jsonl")
    seen = []
    client = client_for(trace, lambda **kw: (seen.append(kw), response(json.dumps(proposal(("ev_1",)))))[1])
    proposer = DeepSeekTaskProposer(client, lambda evidence_id: {"ev_2": "visible excerpt"}[evidence_id])
    update = proposer.propose(snapshot, OnlineTaskAnalyzer().analyze(snapshot), tool_evidence_ids=("ev_2",))
    assert proposer.budget is client.budget and client.budget.requests == 1
    payload = json.loads(seen[0]["messages"][1]["content"])
    assert payload["tool_excerpts"] == [{"evidence_id": "ev_2", "text": "visible excerpt", "truncated": False}]
    assert "scenario" not in payload and "oracle" not in payload
    assert payload["current_tasks"]["subtasks"][0]["actual_status"] == "pending"
    assert update.subtasks[0].certainty == Certainty.INFERRED
    assert update.subtasks[0].status == TaskStatus.PENDING
    assert json.loads(trace.path.read_text(encoding="utf-8").splitlines()[0])["purpose"] == "task_analysis"


def test_task_proposer_bad_json_is_an_explicit_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    recorder = OnlineObservationRecorder()
    recorder.record_notification("user", "work", "ev_1")
    trace = TraceLogger(tmp_path / "usage.jsonl")
    contents = iter(("not JSON", json.dumps(proposal(("ev_1",), task_id="root"))))
    proposer = DeepSeekTaskProposer(client_for(trace, lambda **_: response(next(contents))), lambda _: "unused")
    with pytest.raises(TaskProposalError):
        proposer.propose(recorder.snapshot(), OnlineTaskAnalyzer().analyze(recorder.snapshot()))
    assert "task_proposal_error" in trace.path.read_text(encoding="utf-8")
    with pytest.raises(TaskProposalError, match="root_requires_delivered_user_notification"):
        proposer.propose(recorder.snapshot(), OnlineTaskAnalyzer().analyze(recorder.snapshot()))


def test_one_shot_tool_requirement_binding_and_failure_cleanup(tmp_path):
    controller = Controller(load_config(), TraceLogger(tmp_path / "trace.jsonl"),
                            frozenset({"read"}), frozenset({"MockApp"}), monitor_enabled=True)
    task = controller.provenance.add("user", "trusted_task", "Read requested information")
    controller.online_observations.record_notification("user", task.text, task.evidence_id)
    controller.add_task_update(TaskUpdate(information_requirements=(
        InformationRequirement("root", "Read result", Certainty.INFERRED,
                               (task.evidence_id,), "need_1"),)))
    controller.update(task.text, (), "intake")
    spec, phase = controller.spec, controller.authorization.phase
    context = ObservedContext(controller, task.evidence_id)
    calls = []

    def original(**kwargs):
        calls.append(kwargs)
        if kwargs.get("fail"):
            raise LookupError("original tool failure")
        return "read result"

    original.name, original.description, original.inputs, original.output_type = "read", "read", {}, "any"
    original.app_tool = SimpleNamespace(app_name="MockApp", write_operation=False)
    tool = guard_are_toolset(controller, (original,), context)[0]
    keys = (("root", "need_1"),)
    context.bind_next_tool_requirements(keys)
    assert tool() == "read result"
    linked = controller.online_snapshot().tool_results[-1]
    assert controller.pending_analysis == {linked.event_id: linked}
    assert tool() == "read result"
    logs = [json.loads(line) for line in controller.trace.path.read_text(encoding="utf-8").splitlines()]
    decisions = [row for row in logs if row["kind"] == "prefilter_decision"]
    assert decisions[-1]["reasons"] == ["tool_result_unlinked"]
    assert decisions[-1]["pending_analysis"] is False
    assert decisions[-3]["reasons"] == ["registered_information_result"]
    assert decisions[-3]["event_id"] == linked.event_id
    with pytest.raises(ValueError, match="not_registered"):
        context.bind_next_tool_requirements((("root", "missing"),))
    with pytest.raises(ValueError, match="pairs"):
        context.bind_next_tool_requirements(("root", "need_1"))

    context.bind_next_tool_requirements(keys)
    controller.add_task_update(TaskUpdate(information_requirements=(
        InformationRequirement("root", "Changed need", Certainty.INFERRED,
                               (task.evidence_id,), "need_1"),)))
    call_count = len(calls)
    with pytest.raises(ValueError, match="stale_information_requirement_binding"):
        tool()
    assert len(calls) == call_count
    assert tool() == "read result"  # stale binding was consumed
    context.bind_next_tool_requirements(keys)
    completed_count = len(controller.online_snapshot().tool_results)
    with pytest.raises(LookupError, match="original tool failure"):
        tool(fail=True)
    assert len(controller.online_snapshot().tool_results) == completed_count
    assert tool() == "read result"  # failed call did not leak its binding
    logs = [json.loads(line) for line in controller.trace.path.read_text(encoding="utf-8").splitlines()]
    assert logs[-1]["reasons"] == ["tool_result_unlinked"]
    assert controller.task_analysis.subtasks[0].actual_status == TaskStatus.PENDING
    assert controller.spec is spec and controller.tools.current_spec is spec
    assert controller.messages.current_spec is spec and controller.authorization.phase == phase
    assert all(event.call_id is None for event in controller.online_snapshot().tool_results)


def test_empty_proposal_is_analyzed_but_remains_unverified(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    controller = make_controller(tmp_path, monitor_enabled=True, task_llm_enabled=True)
    recorder = controller.online_observations
    user = recorder.record_notification("user", "work", "ev_1")
    env = recorder.record_notification("environment", "new fact", "ev_2")
    controller.on_observed_event(user)
    controller.on_observed_event(env)
    data = {"source_evidence_ids": ["ev_1", "ev_2"], "subtasks": [],
            "dependencies": [], "information_requirements": []}
    client = client_for(controller.trace, lambda **_: response(json.dumps(data)))
    controller.run_budget = client.budget
    controller.task_proposer = DeepSeekTaskProposer(client, lambda _: "unused")
    decision = controller.process_pending_analysis()
    assert decision.action == TriggerAction.KEEP and "insufficient_evidence" in decision.reasons
    assert controller.pending_analysis == {}
    assert controller.performance_monitor.analyzed_event_ids == {user.event_id, env.event_id}
    assert controller.task_analysis.unresolved_event_ids == (env.evidence_id,)
    assert controller.task_analysis.subtasks[0].actual_status == TaskStatus.PENDING
    logs = [json.loads(line) for line in controller.trace.path.read_text(encoding="utf-8").splitlines()]
    assert logs[-1]["event_ids"] == [user.event_id, env.event_id]
    assert logs[-1]["analysis_succeeded"] is True and logs[-1]["requirements_changed"] is False
    validated = next(row for row in logs if row["kind"] == "task_proposal_validated")
    assert validated["proposal_status"] == "proposed_unverified"


def test_controller_shadow_manual_boundary_dedup_and_episode_request_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    controller = make_controller(tmp_path, monitor_enabled=True, task_llm_enabled=True,
                                 runtime_agent_instances=("agent_0",))
    controller.update("work", (), "intake")
    spec, tool_spec, message_spec = controller.spec, controller.tools.current_spec, controller.messages.current_spec
    authorization_phase = controller.authorization.phase

    def complete(**kw):
        data = json.loads(kw["messages"][1]["content"])
        sources = [event["evidence_id"] for event in data["delivered_notifications"]]
        return response(json.dumps(proposal(sources)))

    client = client_for(controller.trace, complete)
    controller.run_budget = client.budget
    controller.task_proposer = DeepSeekTaskProposer(client, lambda key: controller.provenance.items[key].text)
    seed = controller.provenance.add("startup", "low_trust", "waiting")
    context = ObservedContext(controller, seed.evidence_id)
    for index in range(3):
        context.record_delivered_notification("user", SimpleNamespace(message=f"request {index}"))
        assert client.budget.requests == index  # the ARE callback does not call the model
        event = controller.online_snapshot().user_events[-1]
        controller.on_observed_event(event)
        decision = controller.process_pending_analysis()
        assert decision.action in {TriggerAction.RECONSIDER, TriggerAction.KEEP}
        assert controller.on_observed_event(event).reasons == ("event_already_analyzed",)
    context.record_delivered_notification("environment", SimpleNamespace(message="another update"))
    deferred = controller.process_pending_analysis()
    assert deferred.reasons == ("analysis_deferred_budget",)
    assert len(controller.pending_analysis) == 1 and client.budget.requests == 3
    assert controller.spec is spec and controller.tools.current_spec is tool_spec
    assert controller.messages.current_spec is message_spec
    assert controller.authorization.phase == authorization_phase
    assert controller.task_analysis.subtasks[1].actual_status == TaskStatus.PENDING

    # A transport retry also consumes the same task-analysis request cap.
    from litellm.exceptions import Timeout
    retry_controller = make_controller(tmp_path / "retry", monitor_enabled=True,
                                       task_llm_enabled=True, max_analysis_requests=1)
    calls = []

    def timeout(**kw):
        calls.append(kw)
        raise Timeout("injected offline timeout", "deepseek-flash", "deepseek")

    retry_client = DeepSeekClient(
        RunBudget(retry_controller.trace, max_requests=10, max_total_tokens=100000, max_cost_usd=1),
        DeepSeekSettings(max_output_tokens=128, max_attempts=2), completion_fn=timeout)
    retry_controller.run_budget = retry_client.budget
    retry_controller.task_proposer = DeepSeekTaskProposer(retry_client, lambda _: "unused")
    event = retry_controller.online_observations.record_notification("user", "work", "ev_1")
    retry_controller.on_observed_event(event)
    assert retry_controller.process_pending_analysis().reasons == ("analysis_deferred_budget",)
    assert len(calls) == retry_controller.analysis_requests == retry_client.budget.requests == 1
