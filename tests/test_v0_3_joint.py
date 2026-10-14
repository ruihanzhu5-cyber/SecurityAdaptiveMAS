"""Small offline tests of the v0.3 decision path; no model API calls."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from security_adaptive_mas.are_bridge import ObservedContext
from security_adaptive_mas.config import load_config
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.deepseek import BudgetExceeded, RunBudget
from security_adaptive_mas.dynamic_team import FrameworkTeam
from security_adaptive_mas.graph_runtime import GraphRuntime, TopologyPatch
from security_adaptive_mas.online_task import (Certainty, InformationRequirement,
                                                Subtask, TaskAnalysis, TaskStatus)
from security_adaptive_mas.trace import TraceLogger


def setup(tmp_path, policy="joint"):
    trace = TraceLogger(tmp_path / "joint.jsonl")
    controller = Controller(load_config(), trace, frozenset({"read", "write"}),
                            frozenset({"MockApp"}), monitor_enabled=True)
    controller.organization_policy = policy
    runtime = GraphRuntime(controller, {"read", "write"}, {"read"})
    user = controller.provenance.add("are:user_notification", "trusted_task", "find and send")
    controller.online_observations.record_notification("user", user.text, user.evidence_id)
    controller.task_revision = 1
    return controller, runtime, user


def patch(controller, runtime, name, operations, *, reason="", plans=()):
    observation = controller.online_snapshot()
    return TopologyPatch(name, runtime.graph.version, observation.sequence,
                         controller.task_revision, tuple(operations),
                         delivery_plans=tuple(plans), reason=reason,
                         evidence_ids=observation.evidence_ids)


def task_analysis(controller, user, *, need=False):
    observation = controller.online_snapshot()
    tasks = (Subtask("root", user.text, TaskStatus.PENDING, (user.evidence_id,)),
             Subtask("find", "find needed information", TaskStatus.PENDING, (user.evidence_id,)))
    requirements = ()
    if need:
        tasks += (Subtask("send", "use information", TaskStatus.PENDING, (user.evidence_id,)),)
        requirements = (InformationRequirement("send", "needed finding", Certainty.INFERRED,
                                                (user.evidence_id,), "need", "find"),)
    controller.task_analysis = TaskAnalysis(observation.sequence, tasks, (), requirements,
                                             (user.evidence_id,))


def risk_case(tmp_path, policy):
    controller, runtime, user = setup(tmp_path, policy)
    task_analysis(controller, user, need=True)
    runtime.apply(patch(controller, runtime, "workers", [
        {"op": "SPAWN_WORKER", "agent_id": "worker_bad", "tools": ["read"]},
        {"op": "SPAWN_WORKER", "agent_id": "worker_good", "tools": ["read"]},
        {"op": "ASSIGN", "agent_id": "worker_bad", "task_id": "find"},
        {"op": "ADD_ROUTE", "source": "worker_bad", "target": "agent_0"},
    ]))
    injected = controller.provenance.add("tool", "low_trust", "malicious instruction",
                                         owner_agent="worker_bad")
    bad = controller.provenance.add("runtime:worker_output", "derived", "suspect finding",
                                    parents=(injected.evidence_id,), owner_agent="worker_bad")
    clean = controller.provenance.add("runtime:worker_output", "derived", "independent finding",
                                      parents=(user.evidence_id,), owner_agent="worker_good")
    runtime.add_object(bad.evidence_id, "worker_bad", task_id="find")
    runtime.add_object(clean.evidence_id, "worker_good", task_id="find")
    controller.suspect_instances = {"worker_bad"}
    controller.protected_targets = ("tool:write",)
    keep = patch(controller, runtime, "keep", [{"op": "KEEP"}])
    release = patch(controller, runtime, "release", [
        {"op": "SET_OBJECT_ACCESS", "agent_id": "agent_0", "object_id": bad.evidence_id,
         "allowed": True}], reason="release_output_available", plans=(("agent_0", bad.evidence_id),))
    contain = patch(controller, runtime, "contain", [
        {"op": "REMOVE_ROUTE", "source": "worker_bad", "target": "agent_0"},
        {"op": "RETIRE_WORKER", "agent_id": "worker_bad"}],
        reason="suspicious_worker_output_withheld")
    return controller, runtime, (keep, release, contain), bad, clean


def test_joint_repairs_need_with_independent_object_and_changes_graph(tmp_path):
    controller, runtime, candidates, bad, clean = risk_case(tmp_path, "joint")
    selected = controller.select_runtime_patch(runtime, candidates)
    assert selected.patch_id == "repair_contain"
    assert runtime.read_object("agent_0", clean.evidence_id) == "independent finding"
    assert ("agent_0", bad.evidence_id) not in runtime.graph.object_access
    with pytest.raises(KeyError):
        runtime.read_object("worker_bad", bad.evidence_id)
    assert controller.provenance.contains_low_trust(bad.evidence_id)
    event = next(row for row in controller.trace.path.read_text(encoding="utf-8").splitlines()
                 if '"kind": "joint_decision"' in row)
    assert '"cost_status": "unknown_future"' in event


def test_task_only_uses_same_candidates_without_risk_repair(tmp_path):
    controller, runtime, candidates, bad, _ = risk_case(tmp_path, "task_only")
    selected = controller.select_runtime_patch(runtime, candidates)
    assert selected.patch_id == "release"
    assert ("agent_0", bad.evidence_id) in runtime.graph.object_access


def test_reactive_isolation_reports_task_loss_instead_of_repairing(tmp_path):
    controller, runtime, candidates, bad, _ = risk_case(tmp_path, "reactive_isolation")
    selected = controller.select_runtime_patch(runtime, candidates)
    assert selected.patch_id == "contain"
    assert ("agent_0", bad.evidence_id) not in runtime.graph.object_access
    records = controller.trace.path.read_text(encoding="utf-8")
    assert "simple_reactive_isolation_task_need_unsatisfied_need" in records


def test_posthoc_filter_withholds_risky_task_proposal(tmp_path):
    controller, runtime, _, bad, _ = risk_case(tmp_path, "task_plus_filter")
    runtime.apply(patch(controller, runtime, "remove_route", [
        {"op": "REMOVE_ROUTE", "source": "worker_bad", "target": "agent_0"}]))
    candidates = (
        patch(controller, runtime, "keep", [{"op": "KEEP"}]),
        patch(controller, runtime, "release", [
            {"op": "SET_OBJECT_ACCESS", "agent_id": "agent_0",
             "object_id": bad.evidence_id, "allowed": True}],
            reason="release_output_available", plans=(("agent_0", bad.evidence_id),)),
        patch(controller, runtime, "contain", [
            {"op": "RETIRE_WORKER", "agent_id": "worker_bad"}],
            reason="suspicious_worker_output_withheld"),
    )
    selected = controller.select_runtime_patch(runtime, candidates)
    assert selected.patch_id == "contain"
    assert "posthoc_risk_filter_task_need_unsatisfied_need" in controller.trace.path.read_text(encoding="utf-8")


def test_joint_reports_unrepaired_need_instead_of_dropping_it(tmp_path):
    controller, runtime, candidates, _, clean = risk_case(tmp_path, "joint")
    runtime.objects.pop(clean.evidence_id)
    selected = controller.select_runtime_patch(runtime, candidates)
    assert selected.patch_id == "release"
    records = controller.trace.path.read_text(encoding="utf-8")
    assert 'no_independent_clean_object' in records
    assert 'unsatisfied_need' in records


@pytest.mark.parametrize("policy", ["joint", "task_only", "task_plus_filter", "reactive_isolation"])
def test_one_read_contract_dispatches_one_real_graph_worker(tmp_path, policy):
    controller, runtime, user = setup(tmp_path, policy)
    task_analysis(controller, user)
    controller.execution_contracts["find"] = {
        "task_id": "find", "read_only": True, "required_tools": ["read"],
        "input_evidence_ids": [user.evidence_id], "independent_of": []}
    runtime.add_object(user.evidence_id, "agent_0", status="delivered_notification")
    client = SimpleNamespace(budget=SimpleNamespace(
        max_requests=10, requests=0, actual_usage_by_purpose={}))
    context = ObservedContext(controller, user.evidence_id)
    team = FrameworkTeam(controller, runtime, client, context, {}, "adaptive")
    controller.framework_team = team

    def worker(worker_id, task, contract):
        output = controller.provenance.add("runtime:worker_output", "derived", "finding",
                                            parents=(user.evidence_id,), owner_agent=worker_id)
        runtime.add_object(output.evidence_id, worker_id, task_id=task.task_id)
        controller.online_observations.record_tool_result(
            "runtime:worker_output", output.evidence_id,
            agent_instance_id=worker_id, status="output_available")
        return output.evidence_id

    team.run_worker = worker
    team.dispatch_ready_pair()
    assert team.dispatches == 1
    assert team.delivered_outputs
    assert len(runtime.graph.agents) == 1  # one worker was actually spawned, then retired
    assert runtime.max_active == 2


def test_budget_rejection_is_not_actual_usage_and_purposes_sum(tmp_path):
    trace = TraceLogger(tmp_path / "budget.jsonl")
    budget = RunBudget(trace, max_requests=2, max_total_tokens=10, max_cost_usd=1)
    with pytest.raises(BudgetExceeded):
        budget.before_request([{"role": "user", "content": "long request"}], 10)
    assert budget.requests == budget.input_tokens == budget.output_tokens == 0
    assert "preflight_budget_rejection" in trace.path.read_text(encoding="utf-8")
    budget = RunBudget(trace, max_requests=2, max_total_tokens=100000, max_cost_usd=1)
    budget.before_request([{"role": "user", "content": "x"}], 10, purpose="worker:a")
    budget.after_response(SimpleNamespace(prompt_tokens=12, completion_tokens=3), 0.5,
                          purpose="worker:a")
    assert sum(row["input_tokens"] for row in budget.actual_usage_by_purpose.values()) == budget.input_tokens
    assert budget.actual_usage_by_purpose["worker:a"]["requests"] == 1


def test_exact_duplicate_delivery_is_consolidated_without_losing_need(tmp_path):
    controller, runtime, user = setup(tmp_path)
    task_analysis(controller, user, need=True)
    runtime.apply(patch(controller, runtime, "workers", [
        {"op": "SPAWN_WORKER", "agent_id": "worker_a", "tools": ["read"]},
        {"op": "SPAWN_WORKER", "agent_id": "worker_b", "tools": ["read"]},
        {"op": "ADD_ROUTE", "source": "worker_a", "target": "agent_0"},
        {"op": "ADD_ROUTE", "source": "worker_b", "target": "agent_0"},
    ]))
    first = controller.provenance.add("runtime:worker_output", "derived", "same finding",
                                      parents=(user.evidence_id,), owner_agent="worker_a")
    second = controller.provenance.add("runtime:worker_output", "derived", "same finding",
                                       parents=(user.evidence_id,), owner_agent="worker_b")
    runtime.add_object(first.evidence_id, "worker_a", task_id="find")
    runtime.add_object(second.evidence_id, "worker_b", task_id="find")
    release = patch(controller, runtime, "release", [
        {"op": "SET_OBJECT_ACCESS", "agent_id": "agent_0", "object_id": key, "allowed": True}
        for key in (first.evidence_id, second.evidence_id)],
        reason="release_output_available",
        plans=(("agent_0", first.evidence_id), ("agent_0", second.evidence_id)))
    selected = controller.select_runtime_patch(runtime, [
        patch(controller, runtime, "keep", [{"op": "KEEP"}]), release])
    assert selected.patch_id == "consolidate_release"
    assert len(selected.delivery_plans) == 1
    assert runtime.read_object("agent_0", first.evidence_id) == "same finding"
    with pytest.raises(PermissionError):
        runtime.read_object("agent_0", second.evidence_id)


def test_unchanged_boundary_does_not_repeat_joint_candidate_search(tmp_path):
    controller, runtime, _ = setup(tmp_path)
    controller.select_runtime_patch(runtime, [patch(controller, runtime, "keep", [{"op": "KEEP"}])])
    controller.select_runtime_patch(runtime, [patch(controller, runtime, "keep", [{"op": "KEEP"}])])
    records = controller.trace.path.read_text(encoding="utf-8")
    assert records.count('"kind": "joint_decision"') == 1
    assert records.count('"kind": "joint_reconsideration_deferred"') == 1
