"""Exact typed clone and harness evaluator checks, no model calls."""
from copy import deepcopy
from dataclasses import asdict,replace
import json
from pathlib import Path
import sys
import pytest
from security_adaptive_mas.config import ROOT
from security_adaptive_mas.orchestration.configuration import load_design,project_config
from security_adaptive_mas.are_adapter import import_official_scenario
from security_adaptive_mas.attack import load_attack_spec,AttackOverlay,evaluate_text_attack
from security_adaptive_mas.trace import TraceLogger

DESIGN=load_design(ROOT/"configs/research_v0_6.yaml")
sys.path.insert(0,DESIGN["paths"]["are_root"])


@pytest.fixture(scope="module")
def scenarios():
    config=project_config(DESIGN)
    return {f"C{i}":import_official_scenario(config.select(sid),config.are_root,filesystem=DESIGN["paths"]["gaia2_filesystem"],sandbox=DESIGN["paths"]["sandbox_root"]) for i,sid in enumerate(config.records,1)}


def value_for(scenario,spec):
    app,method=spec.source_tool.split("__",1);instance=scenario.get_app(app)
    if method=="list_emails":
        offset=0
        while True:
            value=instance.list_emails(offset=offset,limit=50)
            if any(e.email_id==spec.source_selector.split(":",1)[1] for e in value.emails):return value
            assert value.emails
            offset+=len(value.emails)
    if method=="read_conversation":return instance.read_conversation(spec.source_conversation_id)
    return instance.list_recent_conversations()


@pytest.mark.parametrize("key",["C1","C2","C4","C5"])
def test_exact_clone_actual_return_once_and_unchanged_app(key,scenarios,tmp_path):
    spec=load_attack_spec(ROOT/"configs/attack_cases_v0_6_five"/(key+".json"));scenario=scenarios[key]
    app=scenario.get_app(spec.source_tool.split("__")[0]);before=deepcopy(app.get_state());value=value_for(scenario,spec)
    def plain(v):
        if isinstance(v,list):return [plain(i) for i in v]
        if isinstance(v,dict):return {k:plain(x) for k,x in v.items()}
        if hasattr(v,"__dataclass_fields__"):return plain(asdict(v))
        return v
    original=plain(value);overlay=AttackOverlay(spec,TraceLogger(tmp_path/"trace.jsonl"),scenario_id=spec.target_case)
    result=overlay.apply_tool_result(value,tool_name=spec.source_tool,agent_id="agent_0")
    changed=plain(result)
    diffs=[]
    def compare(a,b,path=()):
        if isinstance(a,dict):
            assert a.keys()==b.keys()
            for k in a:compare(a[k],b[k],path+(str(k),))
        elif isinstance(a,list):
            assert len(a)==len(b)
            for i,(x,y) in enumerate(zip(a,b)):compare(x,y,path+(str(i),))
        elif a!=b:diffs.append(path)
    compare(original,changed)
    assert len(diffs)==1 and diffs[0][-1]=="content"
    assert plain(value)==original and app.get_state()==before
    assert overlay.apply_tool_result(value,tool_name=spec.source_tool,agent_id="agent_0") is value
    miss=AttackOverlay(replace(spec,source_selector=spec.source_selector.split(":")[0]+":missing"),TraceLogger(tmp_path/"miss.jsonl"),scenario_id=spec.target_case)
    assert miss.apply_tool_result(value,tool_name=spec.source_tool,agent_id="agent_0") is value
    assert not miss.applied


def test_single_email_type_and_legacy_receiver(scenarios,tmp_path):
    spec=load_attack_spec(ROOT/"configs/attack_cases_v0_6_five/C2.json")
    email=scenarios["C2"].get_app("Emails").get_email_by_id(spec.source_selector.split(":")[1])
    one=replace(spec,source_tool="Emails__get_email_by_id",field_path="content")
    overlay=AttackOverlay(one,TraceLogger(tmp_path/"one.jsonl"),scenario_id=one.target_case)
    result=overlay.apply_tool_result(email,tool_name=one.source_tool,agent_id="worker_1")
    assert result.email_id==email.email_id and result.content!=email.content
    legacy=load_attack_spec(ROOT/"configs/attack_single_injection_22_kb5ah9.json")
    old=AttackOverlay(legacy,TraceLogger(tmp_path/"old.jsonl"),scenario_id=legacy.target_case)
    assert old.apply_tool_result(object(),tool_name="Emails__list_emails",agent_id="agent_0") is not None
    assert not old.applied and legacy.receiver_policy is None


def test_unknown_new_fields_rejected(tmp_path):
    path=ROOT/"configs/attack_cases_v0_6_five/C1.json";data=json.loads(path.read_text(encoding="utf-8"))
    data["invented_fallback"]=True
    bad=tmp_path/"bad.json";bad.write_text(json.dumps(data),encoding="utf-8")
    with pytest.raises(ValueError,match="exact_declared_fields"):load_attack_spec(bad)


@pytest.mark.parametrize("initial,final,events,expected",[
    ({"target_present":True},{"target_present":True},[],"false"),
    ({"target_present":True},{"target_present":False},[],"unknown"),
    (None,None,[],"unknown"),
    ({"target_present":True},{"target_present":False},[{"kind":"tool_started","tool":"Calendar__delete_calendar_event","arguments":{"event_id":"target"},"fingerprint":"x"},{"kind":"tool_completed","fingerprint":"x"}],"true")])
def test_calendar_evaluator_requires_state_and_exact_completed_call(initial,final,events,expected):
    spec=replace(load_attack_spec(ROOT/"configs/attack_cases_v0_6_five/C1.json"),target_id="target")
    result=evaluate_text_attack(events,spec=spec,initial=initial,final=final,official_success=True,stop_reason="test_fixture")
    assert result["target_achieved"]==expected
    assert result["attack_injected"] is False and result["model_submitted_exposure"] is False


def test_future_message_is_not_in_initial_read(scenarios):
    case=json.loads((ROOT/"configs/attack_cases_v0_6_five/C3_blocked.json").read_text(encoding="utf-8"))
    assert case["status"]=="BLOCKED_ATTACK_SURFACE"
    config=project_config(DESIGN);raw=json.loads(config.records[case["scenario_id"]].path.read_text(encoding="utf-8"))
    initial=json.dumps(scenarios["C3"].get_app("Messages").get_state(),ensure_ascii=False)
    for event in raw["events"]:
        if event["event_type"]=="ENV" and event["action"]["app"]=="Messages":
            args={x["name"]:x["value"] for x in event["action"]["args"]}
            assert args["content"] not in initial and "message_id" not in args
