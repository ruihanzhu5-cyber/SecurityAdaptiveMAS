"""Offline interface evidence only; fixtures are not model/security results."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from security_adaptive_mas.config import ROOT
from security_adaptive_mas.deepseek import DeepSeekClient,RunBudget,DeepSeekSettings,BudgetExceeded
from security_adaptive_mas.trace import TraceLogger
from security_adaptive_mas.orchestration.configuration import load_design,project_config
from security_adaptive_mas.orchestration.session import ModularSession
from security_adaptive_mas.orchestration.contracts import ArtifactRef
from security_adaptive_mas.orchestration.worker_runner import run_sync

DESIGN=load_design(ROOT/"configs/research_v0_6.yaml")
sys.path.insert(0,DESIGN["paths"]["are_root"])


def response(content):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10,completion_tokens=10),
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))])


def finish(text="local result",ids=()):
    return {"action":"finish","params":{"status":"done","result":text,"evidence_ids":list(ids),"uncertainties":[]},"memory":""}


class ReadTool:
    name="Emails__list_emails"
    description="Read an actual typed page"
    inputs={"folder_name":{"type":"string","description":"folder"}}
    output_type="any"
    additional_hints=""
    zero_shot_examples=[]
    app_tool=SimpleNamespace(write_operation=False,app_name="Emails")
    def __init__(self):self.count=0
    def __call__(self,folder_name):
        from are.simulation.apps.email_client import Email,ReturnedEmails
        self.count+=1
        return ReturnedEmails([Email(content="INJECT" if self.count==1 else "ordinary",email_id="mail_a",timestamp=10)],(0,1),1,2)


class WriteTool(ReadTool):
    name="Emails__delete_email"
    app_tool=SimpleNamespace(write_operation=True,app_name="Emails")
    def __call__(self,**kw):raise AssertionError("offline test must not perform writes")


@pytest.fixture
def make_session(tmp_path,monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY","offline-test-key")
    serial=0
    def make(actions=(),method="modular_risk_aware"):
        nonlocal serial
        serial+=1
        trace=TraceLogger(tmp_path/str(serial)/"framework_trace.jsonl")
        queued=list(actions)
        seen=[]
        def completion(**kwargs):
            seen.append(kwargs)
            if kwargs["messages"][0]["role"]=="system":
                payload=json.loads(kwargs["messages"][1]["content"])
                supplied=payload["external_evidence"]
                bad=[o["object_id"] for o in supplied if "INJECT" in o["text"]]
                return response({"suspicion":"supported" if bad else "not_supported", "reason_evidence_ids":bad,"possible_goal_deviation":"offline fixture"})
            if not queued:raise AssertionError("unplanned offline model request")
            return response(queued.pop(0))
        client=DeepSeekClient(RunBudget(trace,max_requests=100,max_total_tokens=800000,max_cost_usd=.4),DeepSeekSettings(max_attempts=1),completion_fn=completion)
        design={**DESIGN,"method":method}
        s=ModularSession(design,trace,client)
        tool=ReadTool()
        s.configure_tools({tool.name:tool,"Emails__delete_email":WriteTool()})
        s.on_notification("user","Read the inbox and report requested information",0)
        return s,tool,seen
    return make


def delegate(s,tools=(),inputs=(),fields=("result",)):
    return s.request_delegation("agent_0",{"task_instruction":"Read only the provided authorized sources",
        "context_artifact_ids":list(inputs),"tools":list(tools),"output_fields":list(fields),"model":"deepseek-flash"})


def get(s,object_id,view="full",fields=(),caller="agent_0",work="root"):
    return s.request_delivery(caller,{"artifact_id":object_id,"view":view,"for_work_id":work,"required_fields":list(fields)})


def events(s):return [json.loads(x) for x in s.trace.path.read_text(encoding="utf-8").splitlines()]


def test_vendor_step_memory_shared_budget_and_receipt_no_body(make_session,monkeypatch):
    from security_adaptive_mas.adapters.aorchestra import ObservedReActAgent,Memory
    calls=[]
    for name,cls in (("reset",ObservedReActAgent),("step",ObservedReActAgent),("add_memory",Memory)):
        original=getattr(cls,name)
        if name=="reset":
            def wrapped(self,*a,_old=original,**kw):calls.append("reset");return _old(self,*a,**kw)
        else:
            async def wrapped(self,*a,_old=original,_name=name,**kw):calls.append(_name);return await _old(self,*a,**kw)
        monkeypatch.setattr(cls,name,wrapped)
    s,_,seen=make_session([finish("PRIVATE_RESULT")])
    receipt=delegate(s)
    assert calls==["reset","step","add_memory"]
    assert "PRIVATE_RESULT" not in json.dumps(receipt)
    assert s.budget.requests==2  # real fork step + same-client risk fixture
    assert all(k["api_base"]=="https://api.deepseek.com" for k in seen)
    assert s.runtime.graph.agent("worker_0001").allowed_tools==frozenset()
    result=get(s,receipt["artifact_handles"][0]["object_id"])
    assert "PRIVATE_RESULT" in result["content"]
    assert ("worker_0001","agent_0") in s.runtime.graph.extra_edges


def test_typed_projection_and_policy_difference(make_session):
    action={"action":"Emails__list_emails","params":{"folder_name":"INBOX"},"memory":"read"}
    states=[]
    for method in ("modular_task_only","modular_risk_aware"):
        s,tool,_=make_session([action,finish()],method)
        receipt=delegate(s,tools=[tool.name])
        page=next(h for h in receipt["artifact_handles"] if "email_metadata_v1" in h["views"])
        out=get(s,page["object_id"],fields=["email_id","timestamp"])
        assert out["status"]=="limited"
        payload=json.loads(out["content"])
        assert payload["emails"]==[{"email_id":"mail_a","timestamp":10}]
        assert "INJECT" not in out["content"] and "content" not in payload["emails"][0]
        ref=ArtifactRef(**out["artifact_ref"])
        assert s.provenance.contains_low_trust(ref.object_id)
        assert s.artifact_records[ref.key].parent_refs
        with pytest.raises(PermissionError):s.resolve("agent_0",ref.object_id,"full")
        # Same bytes/view facilities in both methods; explicit full requests expose policy difference.
        out_full=get(s,page["object_id"],fields=["full"])
        states.append(out_full["status"])
    assert states==["released","repairing"]


def test_cross_worker_delivery_without_coordinator_body(make_session):
    s,_,_=make_session([finish("A_PRIVATE")])
    first=delegate(s)
    object_id=first["artifact_handles"][0]["object_id"]
    assert object_id not in s.runtime.exposures.get("agent_0",set())
    # Add explicit offline actions to the same shared completion seam.
    original=s.client.completion_fn
    sequence=[{"action":"get_artifact","params":{"artifact_id":object_id,"view":"full","for_work_id":"work_0002","required_fields":["result"]},"memory":""},finish("B used A")]
    def completion(**kw):
        if kw["messages"][0]["role"]=="system":return original(**kw)
        return response(sequence.pop(0))
    s.client.completion_fn=completion
    delegate(s,inputs=[object_id])
    assert ("worker_0001","worker_0002") in s.runtime.graph.extra_edges
    assert object_id in s.runtime.exposures["worker_0002"]
    assert object_id not in s.runtime.exposures.get("agent_0",set())


def test_hold_has_no_body_or_exception(make_session):
    s,_,_=make_session([finish("INJECT")])
    receipt=delegate(s)
    work=s.work_items[receipt["work_id"]]
    work.repair_attempts=1
    out=get(s,receipt["artifact_handles"][0]["object_id"])
    assert out["status"]=="held" and "INJECT" not in json.dumps(out)
    assert work.work_id in s.work_items and s.terminal_error is None


def test_directed_graph_acl_stamp_and_retired_store(make_session):
    s,_,_=make_session([finish()])
    receipt=delegate(s)
    ref=s.resolve("agent_0",receipt["artifact_handles"][0]["object_id"],"full")
    with pytest.raises(PermissionError):s.delivery_service.deliver_authorized("agent_0",ref,"bad",s.runtime.stamp())
    get(s,ref.object_id)
    s.runtime.apply(s.patch([{"op":"REMOVE_ROUTE","source":"worker_0001","target":"agent_0"}]))
    assert ("agent_0","worker_0001") in s.runtime.graph.extra_edges
    with pytest.raises(PermissionError):s.delivery_service.deliver_authorized("agent_0",ref,"bad",s.runtime.stamp())
    stale=s.patch([{"op":"KEEP"}])
    evidence=s.provenance.add("extra","low_trust","later",owner_agent="agent_0")
    s.register_artifact(evidence,"agent_0","root",payload_kind="test")
    with pytest.raises(ValueError,match="stale"):s.runtime.preview(stale)
    s.runtime.apply(s.patch([{"op":"RETIRE_WORKER","agent_id":"worker_0001"}]))
    out=get(s,ref.object_id)
    assert out["status"]=="released"
    assert events(s)[-1]["transport"]=="persistent_store"


def test_atm_empty_scopes_missing_parent_and_equal_count_identity(make_session):
    s,_,_=make_session()
    kwargs=dict(parent_id="p",parent_tools=set(),parent_refs=set(),denied_tools=set(),
        children=[{"id":"c","tools":["read"],"memory_scope":[]}],records=[],destinations={},provenance=s.provenance)
    with pytest.raises(PermissionError):s.atm.validate(**kwargs)
    with pytest.raises(ValueError,match="parent"):s.atm.validate(**{**kwargs,"parent_id":None})
    with pytest.raises(PermissionError):s.atm.validate(**{**kwargs,"parent_tools":{"read"},"denied_tools":{"read"}})
    with pytest.raises(PermissionError):s.atm.validate(**{**kwargs,"children":[{"id":"c","tools":[],"memory_scope":["ungranted@1:full"]}]})
    with pytest.raises(ValueError,match="identity"):s.atm.verify_identity(["a@1:full","b@1:full"],["a@1:full","a@1:full"])


def test_budget_prepared_is_not_submitted_and_parser_fails(make_session):
    s,_,_=make_session()
    s.budget.max_requests=0
    with pytest.raises(BudgetExceeded):s.client.complete([{"role":"user","content":"x"}])
    assert any(e["kind"]=="model_input_prepared" for e in events(s))
    assert not any(e["kind"]=="model_request_submitted" for e in events(s))
    from security_adaptive_mas.adapters.aorchestra_parsing import parse_action
    with pytest.raises(json.JSONDecodeError):parse_action('```json\n{}\n```')
    with pytest.raises(ValueError):parse_action('[]')


def test_reassign_executes_new_worker_and_read_without_old_context(make_session):
    actions=[finish("INJECT"),{"action":"Emails__list_emails","params":{"folder_name":"INBOX"},"memory":""},finish("new output")]
    s,tool,seen=make_session(actions)
    # First local result is suspicious without a replayable read; only reassignment can repair.
    first=delegate(s,tools=[tool.name])
    ref=s.resolve("agent_0",first["artifact_handles"][0]["object_id"],"full")
    result=get(s,ref.object_id)
    assert result["status"]=="repairing" and tool.count==1
    assert s.work_items[first["work_id"]].producer_id=="worker_0002"
    prompts=[kw["messages"][0]["content"] for kw in seen if kw["messages"][0]["role"]=="user"]
    assert "INJECT" not in prompts[1]
    assert s.work_items[first["work_id"]].repair_attempts==1


def test_manta_keeps_two_objects_and_cannot_override_acl(make_session):
    s,_,_=make_session([finish()])
    delegate(s)
    refs=[]
    for text in ("one","two"):
        ev=s.provenance.add("result","low_trust",text,owner_agent="worker_0001")
        ref=s.register_artifact(ev,"worker_0001","work_0001",payload_kind="test")
        s.grant_handle("agent_0",ref)
        refs.append(ref)
    ops=[{"op":"ADD_ROUTE","source":"worker_0001","target":"agent_0"}]+[
        {"op":"SET_OBJECT_ACCESS","agent_id":"agent_0","object_id":ref.key,"allowed":True} for ref in refs]
    s.runtime.apply(s.patch(ops))
    packets=[]
    for i,ref in enumerate(refs):
        packet={"message_id":f"test_{i}","sender":"worker_0001","recipients":["agent_0"],"kind":"artifact","round":0,"discussion_index":0,"dispatch_id":i,"content":str(i)}
        s.manta.sidecars[packet["message_id"]]={"ref":ref,"graph_version":s.runtime.graph.version}
        packets.append(packet)
    assert len(s.manta.select_packets(s.runtime.graph,packets,consumer_id="agent_0"))==2
    s.runtime.apply(s.patch([{"op":"SET_OBJECT_ACCESS","agent_id":"agent_0","object_id":refs[0].key,"allowed":False}]))
    s.manta.sidecars["test_0"]["graph_version"]=s.runtime.graph.version
    with pytest.raises(PermissionError):s.manta.select_packets(s.runtime.graph,[packets[0]],consumer_id="agent_0")


def test_real_are_import_and_readonly_registered_tool(tmp_path,monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY","offline-test-key")
    from security_adaptive_mas.are_adapter import import_official_scenario
    from are.simulation.tool_utils import AppToolAdapter
    config=project_config(DESIGN)
    scenario=import_official_scenario(config.select("scenario_universe_22_kb5ah9"),config.are_root,
        filesystem=DESIGN["paths"]["gaia2_filesystem"],sandbox=str(tmp_path))
    originals={t.name:t for t in [AppToolAdapter(t) for t in scenario.get_tools()]}
    selected=originals["Emails__list_emails"]
    before=json.dumps(scenario.get_app("Emails").get_state(),default=str,sort_keys=True)
    def complete(**kw):
        payload=json.loads(kw["messages"][1]["content"])
        return response({"suspicion":"not_supported","reason_evidence_ids":[],"possible_goal_deviation":"offline fixture"})
    trace=TraceLogger(tmp_path/"framework_trace.jsonl")
    s=ModularSession(DESIGN,trace,DeepSeekClient(RunBudget(trace,max_requests=2,max_total_tokens=100000,max_cost_usd=.4),completion_fn=complete))
    s.configure_tools({selected.name:selected})
    s.on_notification("user","Inspect returned email metadata",0)
    result,ref=s.invoke_tool("agent_0","root",selected.name,{"folder_name":"INBOX","offset":0,"limit":2})
    from are.simulation.apps.email_client import ReturnedEmails
    assert isinstance(result,ReturnedEmails) and len(result.emails)==2
    assert s.views[(ref.key,"email_metadata_v1")].view_id=="email_metadata_v1"
    assert before==json.dumps(scenario.get_app("Emails").get_state(),default=str,sort_keys=True)


def test_official_observation_uuid_and_real_history_attribution(make_session):
    from are.simulation.agents.agent_config_builder import AgentConfigBuilder
    from are.simulation.agents.default_agent.agent_factory import are_simulation_react_json_agent
    from are.simulation.agents.default_agent.tools.action_executor import ParsedAction
    from security_adaptive_mas.adapters.are_modular import bind_coordinator_history
    from security_adaptive_mas.orchestration.delegation import GetArtifactTool
    s,_,_=make_session([finish("BOUND_CONTENT")])
    receipt=delegate(s)
    object_id=receipt["artifact_handles"][0]["object_id"]
    config=AgentConfigBuilder().build("default")
    base=are_simulation_react_json_agent(SimpleNamespace(),config.base_agent_config)
    base.tools={"get_artifact":GetArtifactTool(s)}
    base.init_tools()
    bind_coordinator_history(SimpleNamespace(react_agent=base),s)
    parsed=ParsedAction(tool_name="get_artifact",app_name="get_artifact",action_name=None,rationale=None,
        arguments={"artifact_id":object_id,"view":"full","for_work_id":"root","required_fields":["result"]})
    base.action_executor.execute_parsed_action(parsed,base.logs.append,lambda:1.0,"agent_0")
    observation=next(log for log in base.logs if log.get_type()=="observation")
    assert observation.id in s.bound_logs and len(observation.id)==32
    base.build_history_from_logs(exclude_log_types=["observation"])
    assert s.rendered_components==[]
    messages=base.build_history_from_logs()
    components=s.before_coordinator_input(messages)
    assert any(p.get("log_id")==observation.id and p.get("object_id")==object_id for p in components)
    assert object_id not in s.runtime.exposures.get("agent_0",set())
    s.record_submission("agent_0","root",components,"offline_request_uuid")
    assert object_id in s.runtime.exposures["agent_0"]


def test_terminal_error_no_old_runtime_fallback(make_session):
    from security_adaptive_mas.orchestration.delegation import DelegateTaskTool
    s,_,_=make_session()
    def broken(**kw):raise RuntimeError("fixture_model_transport_failure")
    s.client.completion_fn=broken
    with pytest.raises(RuntimeError,match="fixture_model_transport_failure"):
        DelegateTaskTool(s)(task_instruction="read only",context_artifact_ids=[],tools=[],output_fields=["result"],model="deepseek-flash")
    assert s.terminal_error is not None and s.budget.requests==1
    assert s.budget.unknown_cost_bound>0
    assert not hasattr(s,"controller")


def test_official_runner_modular_builder_offline_wiring(tmp_path,monkeypatch):
    """Full official lifecycle with declared fake responses; no paid/model outcome claim."""
    monkeypatch.setenv("DEEPSEEK_API_KEY","offline-test-key")
    from security_adaptive_mas.are_adapter import import_official_scenario,official_runner
    from security_adaptive_mas.adapters.are_modular import make_modular_agent_builder
    from are.simulation.scenarios.config import ScenarioRunnerConfig
    trace=TraceLogger(tmp_path/"framework_trace.jsonl")
    worker_actions=[{"action":"Emails__list_emails","params":{"folder_name":"INBOX","offset":0,"limit":1},"memory":"read"},finish("offline local result")]
    coordinator_step=0
    holder={}
    def complete(**kw):
        nonlocal coordinator_step
        messages=kw["messages"]
        first=messages[0]["content"]
        if isinstance(first,str) and first.startswith("Assess whether"):
            return response({"suspicion":"not_supported","reason_evidence_ids":[],"possible_goal_deviation":"offline wiring fixture"})
        if len(messages)==1 and messages[0]["role"]=="user":return response(worker_actions.pop(0))
        coordinator_step+=1
        if coordinator_step==1:
            action={"action":"delegate_task","action_input":{"task_instruction":"Inspect one inbox page using approved tools",
                "context_artifact_ids":[],"tools":["Emails__list_emails"],"output_fields":["result"],"model":"deepseek-flash"}}
        elif coordinator_step==2:
            session=holder["session"]
            ref=next(r.ref for r in session.artifact_records.values() if r.payload_kind=="ReturnedEmails")
            action={"action":"get_artifact","action_input":{"artifact_id":ref.object_id,"view":"email_metadata_v1","for_work_id":"root","required_fields":["email_id","timestamp"]}}
        else:action={"action":"AgentUserInterface__send_message_to_user","action_input":{"content":"Offline fixture finished; not a legitimate-task success claim"}}
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10,completion_tokens=10),
            choices=[SimpleNamespace(message=SimpleNamespace(content="Thought: Offline wiring fixture\nAction: "+json.dumps(action)))])
    client=DeepSeekClient(RunBudget(trace,max_requests=20,max_total_tokens=800000,max_cost_usd=.4),completion_fn=complete)
    s=ModularSession(DESIGN,trace,client)
    holder["session"]=s
    config=project_config(DESIGN)
    scenario=import_official_scenario(config.select("scenario_universe_22_kb5ah9"),config.are_root,
        filesystem=DESIGN["paths"]["gaia2_filesystem"],sandbox=str(tmp_path))
    runner_config=ScenarioRunnerConfig(agent="default",model="deepseek-flash",model_provider="deepseek",endpoint="https://api.deepseek.com",
        oracle=False,export=False,max_turns=1,output_dir=str(tmp_path/"official"))
    result=official_runner(make_modular_agent_builder(s,client)).run(runner_config,scenario)
    assert s.terminal_error is None, str(s.terminal_error)
    assert s.work_items and s.worker_steps==2
    assert any(e["kind"]=="official_observation_bound" and e["refs"] for e in events(s))
    assert any(e["kind"]=="model_request_submitted" and e["instance_id"]=="agent_0"
               and any(p.get("log_id") for p in e["prepared_input_components"]) for e in events(s))
