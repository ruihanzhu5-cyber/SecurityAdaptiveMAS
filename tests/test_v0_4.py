"""Offline engineering acceptance; no API key or paid completion is used."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from security_adaptive_mas.config import ROOT, load_config
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.deepseek import DeepSeekClient, DeepSeekSettings, RunBudget, BudgetExceeded
from security_adaptive_mas.dynamic_team import FrameworkTeam
from security_adaptive_mas.are_bridge import ObservedContext
from security_adaptive_mas.graph_runtime import GraphRuntime
from security_adaptive_mas.organization_contracts import RoleContract, NeedContract, load_design, UnsupportedDesign
from security_adaptive_mas.online_task import Subtask, TaskStatus, Certainty, TaskUpdate, TaskDependency
from security_adaptive_mas.risk_monitor import RiskMonitor
from security_adaptive_mas.topology_candidates import TOPOLOGY_FAMILIES, generate_edges
from security_adaptive_mas.trace import TraceLogger
from security_adaptive_mas.experiment_suite import summarize_units, blackbox_search


class OfflineClient:
    def __init__(self, budget):
        self.budget = budget
        self.calls = []

    def complete(self,messages,**kwargs):
        self.calls.append((messages,kwargs))
        instruction = json.loads(messages[0]["content"])
        ids = list(dict.fromkeys(p["object_id"] for p in kwargs["input_components"] if "object_id" in p))
        kwargs["on_submitted"]("offline_request_"+str(len(self.calls)))
        output = {"fields":{key:instruction["role_contract"]["role_id"]+" finding"
                            for key in instruction["role_contract"]["output_contract"]},
                  "evidence_ids":ids,"uncertainties":[]}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(output),tool_calls=[]))])


def system(tmp_path,family="directed_chain",protocol="dependency_schedule",method="task_only",count=3):
    design = load_design(ROOT/"configs/research_v0_4.yaml")
    design.update(topologies=[family],protocol=protocol,risk_detector="regex",method=method)
    c = Controller(load_config(),TraceLogger(tmp_path/"trace.jsonl"),frozenset({"read"}),frozenset({"local"}))
    c.design=design; c.suspect_instances=set()
    runtime=GraphRuntime(c,{"read"},{"read"},8)
    user=c.provenance.add("are:user_notification","trusted_task","Analyze local synthetic findings")
    c.online_observations.record_notification("user",user.text,user.evidence_id)
    runtime.add_object(user.evidence_id,"agent_0",status="delivered_notification")
    tasks=tuple(Subtask(f"task_{i}",f"Local task {i}",TaskStatus.PENDING,(user.evidence_id,)) for i in range(count))
    c.add_task_update(TaskUpdate(subtasks=tasks,source_evidence_ids=(user.evidence_id,)))
    roles=[]
    for i,task in enumerate(tasks):
        role=RoleContract(f"role{i}",task.task_id,task.description,(f"method {i}",),
            (user.evidence_id,),(),(f"field{i}",),
            {"kind":"artifact_schema","required_fields":[f"field{i}"],"require_evidence":True},
            "reuse",2,(user.evidence_id,))
        roles.append(role); c.role_specs[role.role_id]=role
    budget=RunBudget(c.trace,max_requests=100,max_total_tokens=800000,max_cost_usd=1)
    c.run_budget=budget
    client=OfflineClient(budget)
    context=ObservedContext(c,user.evidence_id)
    team=FrameworkTeam(c,runtime,client,context,{},"adaptive")
    c.framework_team=team
    team.risk_monitor=RiskMonitor(client,c,"regex")
    return c,runtime,team,roles,user


@pytest.mark.parametrize("family",TOPOLOGY_FAMILIES)
def test_each_topology_compiles_and_executes_actual_role_delivery(tmp_path,family):
    c,runtime,team,roles,user=system(tmp_path,family,protocol="bounded_review")
    if family == "singleton":
        team.dispatch_roles()
        assert not team.client.calls  # official Coordinator continuation, no simulated singleton worker
        return
    # v0.5 no longer dispatches merely to reward more agents/deliveries.
    # Register a concrete output need on a real direct route to the Coordinator.
    members = tuple(f"fixture_{i}" for i in range(len(roles))) + ("agent_0",)
    edges = generate_edges(family,members,assignments={r.task_id:members[i] for i,r in enumerate(roles)},
                           dependencies=(),parameters=c.design["topology_parameters"],seed=c.design["seed"])
    provider = next(a for a,b in edges if b == "agent_0")
    index = members.index(provider)
    c.need_specs["topology_result"] = NeedContract("topology_result","root","proposed_plan",(user.evidence_id,),
        ({"task_id":roles[index].task_id,"output_slot":"result"},),
        {"required_fields":[f"field{index}"],"independent_from":[]})
    if family == "directed_chain":
        for i in (1,2):
            c.need_specs[f"chain_{i}"] = NeedContract(f"chain_{i}",f"task_{i}","proposed_plan",(user.evidence_id,),
                ({"task_id":f"task_{i-1}","output_slot":"result"},),
                {"required_fields":[f"field{i-1}"],"independent_from":[]})
        c.add_task_update(TaskUpdate(dependencies=tuple(TaskDependency(f"task_{i-1}",f"task_{i}",
            Certainty.INFERRED,(user.evidence_id,)) for i in (1,2)),source_evidence_ids=(user.evidence_id,)))
    team.dispatch_roles()
    assert team.client.calls
    assert len(runtime.objects) >= 4  # compact one-round plan may now beat two rounds
    assert runtime.flow_events
    if family == "directed_chain":
        received = [e for e in runtime.flow_events if e["flow_kind"]=="object_delivered"
                    and e["transport"]=="live_route" and e["instance_id"]!="agent_0"]
        assert received
        assert all(e["author_instance_id"]!="agent_0" for e in received)


def test_planned_dependency_waits_for_actual_checked_output(tmp_path):
    c,runtime,team,roles,user=system(tmp_path,count=2)
    c.add_task_update(TaskUpdate(dependencies=(TaskDependency("task_0","task_1",Certainty.INFERRED,(user.evidence_id,)),),
                                source_evidence_ids=(user.evidence_id,)))
    c.need_specs["input"] = NeedContract("input","task_1","proposed_plan",(user.evidence_id,),
        ({"task_id":"task_0","output_slot":"result"},),{"required_fields":["field0"],"independent_from":[]})
    c.need_specs["root_result"] = NeedContract("root_result","root","proposed_plan",(user.evidence_id,),
        ({"task_id":"task_1","output_slot":"result"},),{"required_fields":["field1"],"independent_from":[]})
    original_complete=team.client.complete
    def checked_complete(messages,**kwargs):
        if not team.client.calls:
            with pytest.raises(PermissionError,match="requires_actual_checked_inputs"):
                team.require_sensitive_inputs("agent_0")
        return original_complete(messages,**kwargs)
    team.client.complete=checked_complete
    team.dispatch_roles()
    assert len(team.client.calls)==2
    second_messages=team.client.calls[1][0]
    assert any("role0 finding" in m["content"] for m in second_messages)
    assert team.delivered_outputs==[runtime.bindings[("task_1","result")]]
    from security_adaptive_mas.graph_evaluation import plan_need_status
    assert all(n["status"]=="satisfied" for n in plan_need_status(c,runtime,team.active_plan))
    team.require_sensitive_inputs("agent_0")


def test_retired_author_storage_delivery_and_no_preview_side_effects(tmp_path):
    c,runtime,team,roles,user=system(tmp_path,count=1,family="star")
    c.need_specs["root_result"] = NeedContract("root_result","root","proposed_plan",(user.evidence_id,),
        ({"task_id":"task_0","output_slot":"result"},),{"required_fields":["field0"],"independent_from":[]})
    team.dispatch_roles()
    writer=dict(team.active_plan.role_specs).popitem()[0]
    key=runtime.bindings[("task_0","result")]
    before=list(runtime.flow_events)
    patch=team.patch("retire",[{"op":"RETIRE_WORKER","agent_id":writer}])
    runtime.preview(patch)
    assert runtime.flow_events==before
    runtime.apply(patch)
    runtime.graph.object_access.add(("agent_0",key))
    runtime.receipts.discard(("agent_0",key,1))
    runtime.deliver_object("agent_0",key)
    assert runtime.flow_events[-1]["transport"]=="persistent_store"
    assert runtime.objects[key]["writer"]==writer


def test_budget_rejection_has_no_submission_or_exposure(tmp_path,monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY","offline-test-not-a-real-key")
    budget=RunBudget(TraceLogger(tmp_path/"budget.jsonl"),max_requests=0)
    client=DeepSeekClient(budget,DeepSeekSettings(max_attempts=1),completion_fn=lambda **kw:pytest.fail("must not submit"))
    with pytest.raises(BudgetExceeded):
        client.complete([{"role":"user","content":"local"}],input_components=[{"object_id":"fixture"}],instance_id="test")
    rows=[json.loads(line) for line in budget.trace.path.read_text().splitlines()]
    assert any(r["kind"]=="model_input_prepared" for r in rows)
    assert not any(r["kind"]=="model_request_submitted" for r in rows)


def test_explicit_modes_and_delaunay_degeneracy(tmp_path):
    design=load_design(ROOT/"configs/research_v0_4.yaml")
    design["scheduling"]="concurrent_reads"
    import yaml
    path=tmp_path/"bad.yaml"; path.write_text(yaml.safe_dump(design))
    with pytest.raises(UnsupportedDesign,match="BLOCKED"):
        load_design(path)
    with pytest.raises(UnsupportedDesign,match="collinear"):
        generate_edges("geometric_delaunay",("a","b","c"),assignments={},dependencies=(),
                       parameters={"coordinates":[[0,0],[1,1],[2,2]]},seed=1)


def test_unknown_outcomes_and_blackbox_attempt_budget(tmp_path):
    rows=[{"eligible":True,"target_achieved":None,"model_exposed":True,"injection_delivered":True,
           "legitimate_completed":True,"harm_observed":None,"stop_reason":"done",
           "evidence_status":"unknown","cost_estimate_usd":.1}]
    assert summarize_units(rows)["asr_bounds"]==[0,1]
    assert summarize_units(rows)["safe_completed"]==0
    attempts=blackbox_search(defense_version="frozen",attempts=4,initial_candidate="synthetic note",
        generate=lambda candidate,feedback:(candidate+".",0.0),
        run_victim=lambda candidate,version:{"run_id":"engineering-fixture","code_snapshot_id":version,"cost_estimate_usd":0,"unknown_cost_bound":0},
        evaluate=lambda victim:({"target_achieved":None},0),observation_policy="success_only",
        output=tmp_path/"attempts.jsonl",editable_scope="synthetic local fixture")
    assert len(attempts)==4
    assert all(set(a.feedback)=={"target_achieved"} for a in attempts)


def test_real_are_gateway_requery_keeps_external_source_label(tmp_path):
    c,runtime,team,roles,user=system(tmp_path,count=1,family="star")
    from security_adaptive_mas.are_adapter import import_official_scenario
    config=load_config()
    scenario=import_official_scenario(config.select("scenario_universe_22_kb5ah9"),config.are_root)
    from are.simulation.tool_utils import AppToolAdapter
    name="Emails__list_emails"
    team.originals={name:AppToolAdapter(next(t for t in scenario.get_tools() if t._public_name==name))}
    before=str(scenario.get_app("Emails").get_state())
    runtime.worker_tools=frozenset({name})
    c.authorized_tools=frozenset({name}); c.tools.authorized_tools=c.authorized_tools
    c.tools.authorized_resources=frozenset({"Emails"})
    role=replace(roles[0],requested_tools=(name,))
    c.role_specs[role.role_id]=role
    polluted=c.provenance.add("fixture:external_old","low_trust","unverified old claim")
    runtime.add_object(polluted.evidence_id,"agent_0")
    c.need_specs["replacement"] = NeedContract("replacement","root","proposed_plan",(user.evidence_id,),
        ({"task_id":"task_0","output_slot":"result"},),
        {"required_fields":["field0"],"independent_from":[polluted.evidence_id]})
    c.repair_specs=({"need_id":"replacement","kind":"requery","role_id":role.role_id,
        "independent_from":[polluted.evidence_id],"method":"read local independent source","source_ids":[user.evidence_id]},)
    original_complete=team.client.complete
    used=False
    def complete(messages,**kwargs):
        nonlocal used
        if not used:
            used=True
            kwargs["on_submitted"]("offline_tool_request")
            call=SimpleNamespace(id="local_call",function=SimpleNamespace(name=name,
                arguments=json.dumps({"folder_name":"INBOX","offset":0,"limit":1})))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="",tool_calls=[call]))])
        return original_complete(messages,**kwargs)
    team.client.complete=complete
    team.dispatch_roles()
    assert str(scenario.get_app("Emails").get_state())==before
    assert sum(json.loads(line)["kind"]=="tool_started" for line in c.trace.path.read_text().splitlines())==1
    output_id=runtime.bindings[("task_0","result")]
    assert runtime.objects[output_id]["low_trust"] is True
    assert not c.provenance.derived_from(output_id,polluted.evidence_id)
    assert runtime.checks[output_id]["checker"]=="artifact_schema_not_semantic_truth"
    assert team.delivered_outputs==[output_id]


def test_semantic_monitor_shared_budget_and_canonical_risk_trigger(tmp_path,monkeypatch):
    c,runtime,team,roles,user=system(tmp_path,count=1,family="star")
    monkeypatch.setenv("DEEPSEEK_API_KEY","offline-test-not-a-real-key")
    obj=c.provenance.add("fixture:external","low_trust","synthetic inconsistent guidance")
    runtime.add_object(obj.evidence_id,"agent_0")
    seen=[]
    def completion(**kwargs):
        seen.append(kwargs["messages"])
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=20,completion_tokens=10),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "suspicion":"supported","reason_evidence_ids":[obj.evidence_id],
                "possible_goal_deviation":"unnecessary work"})))])
    client=DeepSeekClient(c.run_budget,DeepSeekSettings(max_attempts=1),completion_fn=completion)
    monitor=RiskMonitor(client,c,"semantic")
    risk=monitor.assess("agent_0",(obj.evidence_id,))
    from security_adaptive_mas.joint_rewriter import joint_trigger
    trigger=joint_trigger(c.online_snapshot(),c._current_task_analysis(c.online_snapshot()),runtime,risk_events=[risk])
    assert "observed_risk_boundary" in trigger.reasons
    assert trigger.affected_agent_ids==("agent_0",)
    assert c.run_budget.requests_by_purpose["risk_assessment"]==1
    assert "attack_id" not in json.dumps(seen)
    assert c.run_budget.actual_usage_by_purpose["risk_assessment"]["input_tokens"]==20


def test_role_schema_and_empirical_missing_calibration_are_explicit(tmp_path):
    with pytest.raises(ValueError,match="RoleContract"):
        RoleContract.parse({"role_id":"title only"},tools=(),evidence_ids=(),task_ids=(),max_steps=6)
    import yaml
    design=load_design(ROOT/"configs/research_v0_4.yaml")
    design["cost_mode"]="empirical"; design["calibration_file"]=str(tmp_path/"missing.json")
    path=tmp_path/"empirical.yaml"; path.write_text(yaml.safe_dump(design))
    with pytest.raises(FileNotFoundError,match="BLOCKED"):
        load_design(path)


def test_role_tools_differ_and_reassignment_keeps_exposure(tmp_path):
    c,runtime,team,roles,user=system(tmp_path,count=2,family="star")
    first=replace(roles[0],requested_tools=("read",))
    c.role_specs[first.role_id]=first
    team.originals={"read":SimpleNamespace(name="read",description="fixture catalog read",inputs={},
        output_type="string",app_tool=SimpleNamespace(app_name="local",write_operation=False))}
    c.need_specs["root_result"] = NeedContract("root_result","root","proposed_plan",(user.evidence_id,),
        ({"task_id":"task_1","output_slot":"result"},),{"required_fields":["field1"],"independent_from":[]})
    team.dispatch_roles()
    assert team.client.calls[0][1]["tools"] and team.client.calls[1][1]["tools"] is None
    assert json.loads(team.client.calls[0][0][0]["content"])["role_contract"]["procedure"] != json.loads(team.client.calls[1][0][0]["content"])["role_contract"]["procedure"]
    instance=runtime.graph.assignments["task_0"]
    old=set(runtime.exposures[instance])
    changed=replace(first,role_id="reassigned",objective="new local responsibility")
    runtime.apply(team.patch("reassign",[{"op":"SET_ROLE","agent_id":instance,"role":changed}]))
    assert runtime.exposures[instance]==old
    assert team.role_histories[instance]


def test_delegation_retains_sandbox_and_attachment_boundaries(tmp_path):
    c,runtime,team,roles,user=system(tmp_path,count=1)
    from security_adaptive_mas.security import SandboxPermissivePolicy
    policy=SandboxPermissivePolicy(c.provenance,c.trace,{"Emails__delete_email","Emails__send_email"})
    policy.register_user(user.evidence_id)
    policy.delegated_capabilities["scoped_role"]={"Emails__delete_email","Emails__send_email"}
    assert policy.permits("scoped_role",1,"Emails__delete_email","write","Emails",{"email_id":"synthetic"},user.evidence_id)
    assert not policy.permits("other_role",1,"Emails__delete_email","write","Emails",{"email_id":"synthetic"},user.evidence_id)
    assert not policy.permits("scoped_role",1,"Emails__send_email","write","Emails",{"attachment_paths":["C:/host"]},user.evidence_id)


def test_empirical_multioutput_fit_contract_on_explicit_engineering_fixture(tmp_path):
    import random
    from security_adaptive_mas.cost_model import FEATURES,TARGETS,fit_development
    rng=random.Random(7)
    rows=[]
    for index in range(12):
        values=[rng.random()*10 for _ in FEATURES]
        rows.append({"split":"development","run_id":"engineering_fixture_"+str(index),
            "evidence_status":"actual_usage","source_trace_sha256":"engineering_fixture_not_experiment",
            "features":dict(zip(FEATURES,values)),
            "actual_remaining":{key:10+sum((i+1)*v for i,v in enumerate(values)) for key in TARGETS}})
    result=fit_development(rows,tmp_path/"fixture_calibration.json")
    assert result["targets"]==list(TARGETS)
    assert len(result["coefficients"])==5
    assert max(result["mae"])<1e-8
    assert result["error_scope"]=="training_residual_not_heldout_error"


def test_v04_model_proposal_enters_controller_without_changing_user_goal(tmp_path,monkeypatch):
    from dataclasses import asdict
    from security_adaptive_mas.task_proposer import DeepSeekTaskProposer
    from security_adaptive_mas.organization_contracts import GoalContract
    c,runtime,team,roles,user=system(tmp_path,count=0)
    c.monitor_enabled=True; c.task_llm_enabled=True
    event=c.online_snapshot().user_events[0]
    c.pending_analysis[event.event_id]=event
    role=RoleContract("local_reader","new_task","Read local data",("inspect available evidence",),
        (user.evidence_id,),(),("finding",),
        {"kind":"artifact_schema","required_fields":["finding"],"require_evidence":True},
        "reuse",2,(user.evidence_id,))
    payload={"source_evidence_ids":[user.evidence_id],"subtasks":[{"task_id":"new_task",
        "description":"Read local data","suggested_status":"pending","evidence_ids":[user.evidence_id],"certainty":"inferred"}],
        "dependencies":[],"information_requirements":[],"role_specs":[asdict(role)],
        "need_specs":[{"need_id":"finding","consumer_task_id":"root","origin":"proposed_plan",
            "evidence_ids":[user.evidence_id],"provider_alternatives":[{"task_id":"new_task","output_slot":"result"}],
            "acceptance_contract":{"required_fields":["finding"],"independent_from":[]}}]}
    monkeypatch.setenv("DEEPSEEK_API_KEY","offline-test-not-a-real-key")
    def completion(**kwargs):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=20,completion_tokens=20),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])
    client=DeepSeekClient(c.run_budget,DeepSeekSettings(max_attempts=1),completion_fn=completion)
    c.task_proposer=DeepSeekTaskProposer(client,lambda key:c.provenance.items[key].text,
        execution_tools=(),design=c.design,organization_snapshot=lambda:{"available_object_ids":list(runtime.objects)})
    original=GoalContract.from_observation(c.online_snapshot())
    c.process_pending_analysis()
    assert c.role_specs[role.role_id]==role
    assert c.need_specs["finding"].status=="unknown"
    assert GoalContract.from_observation(c.online_snapshot())==original
    assert c.run_budget.task_analysis_requests==1
