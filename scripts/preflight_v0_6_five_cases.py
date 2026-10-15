"""Five fixed scenarios: official imports and typed read probes, zero model calls."""
import argparse
import hashlib
import json
import traceback
from pathlib import Path
from types import SimpleNamespace

from security_adaptive_mas.config import ROOT
from security_adaptive_mas.are_adapter import import_official_scenario,import_benchmark_scenario
from security_adaptive_mas.deepseek import DeepSeekClient,RunBudget
from security_adaptive_mas.orchestration.configuration import load_design,project_config
from security_adaptive_mas.security import SandboxPermissivePolicy
from security_adaptive_mas.attack import load_attack_spec,AttackOverlay,attack_target_state
from security_adaptive_mas.trace import TraceLogger
from security_adaptive_mas.run_framework import save


def benchmark_preflight(output,scenario_ids=None):
    """Actual official reference preprocessing, never model calls or agent claims."""
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    design=load_design(ROOT/"configs/research_v0_6.yaml");config=project_config(design)
    rows=json.loads((ROOT/"configs/five_manifest.json").read_text(encoding="utf-8"))
    results=[]
    def forbidden(**kwargs):raise RuntimeError("Benchmark compatibility preflight cannot call a model")
    for index,row in enumerate(rows,1):
        if scenario_ids is not None and row["scenario_id"] not in scenario_ids:continue
        directory=output/f"C{index}";directory.mkdir(parents=True,exist_ok=True)
        client=DeepSeekClient(RunBudget(TraceLogger(directory/"framework_trace.jsonl"),100,800000,.4),completion_fn=forbidden)
        result={"case_id":f"C{index}","scenario_id":row["scenario_id"],"scenario_sha256":row["sha256"],
            "legitimate_scope":"PARTIAL_SCOPE" if index==4 else "FULL",
            "attack_constraint":"BLOCKED_ATTACK_SURFACE" if index==3 else "existing_frozen_configuration",
            "paid_api_requests":0}
        try:
            scenario=import_benchmark_scenario(config.select(row["scenario_id"]),config.are_root,client=client,
                filesystem=design["paths"]["gaia2_filesystem"],sandbox=design["paths"]["sandbox_root"])
            result.update(status="READY_BENCHMARK_IMPORT_ONLY",metadata=scenario.benchmark_runtime_metadata,
                event_count=len(scenario.events),oracle_reference_event_count=len(scenario.oracle_run_event_log),
                condition_event_ids=[e.event_id for e in scenario.events if e.event_type.value=="CONDITION"])
            save(directory/"event_graph.json",[e.to_dict() for e in scenario.events])
        except Exception as error:
            # A diagnostic boundary, not a runtime fallback: retain the exact
            # source failure and make this case ineligible for paid execution.
            result.update(status="BLOCKED_EVALUATION",exception_type=type(error).__name__,
                reason=str(error),traceback=traceback.format_exc())
        result["model_requests"]=client.budget.requests
        save(directory/"benchmark_compatibility.json",result);results.append(result)
    save(output/"benchmark_compatibility_matrix.json",results)
    return results


def preflight(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    design=load_design(ROOT/"configs/research_v0_6.yaml");config=project_config(design)
    source=json.loads((ROOT/"configs/source_versions.json").read_text(encoding="utf-8"))
    rows=json.loads((ROOT/"configs/five_manifest.json").read_text(encoding="utf-8"))
    if len(rows)!=5 or len({r["scenario_id"] for r in rows})!=5:raise ValueError("five_fixed_cases_required")
    matrix=[]
    for i,row in enumerate(rows,1):
        key=f"C{i}";sid=row["scenario_id"];directory=output/key;directory.mkdir(exist_ok=True)
        result={"case_id":key,"scenario_id":sid,"scenario_sha256":row["sha256"],"scenario_sha256_ok":False,
            "official_are_commit":source["source_commit"],"dataset_revision":source["dataset_revision"],
            "model_calls":0,"candidate_read_surface":None,"attack_target":None,"injection_adapter_supported":False,
            "legitimate_scope":"FULL","status":"BLOCKED_ATTACK_SURFACE","benign_ready":False,"attack_ready":False}
        try:
            record=config.select(sid);result["scenario_sha256_ok"]=True
            scenario=import_official_scenario(record,config.are_root,filesystem=design["paths"]["gaia2_filesystem"],sandbox=design["paths"]["sandbox_root"])
            raw=json.loads(record.path.read_text(encoding="utf-8"))
            tools={t._public_name or t.name:SimpleNamespace(app_tool=t) for t in scenario.get_tools() if type(t.write_operation) is bool}
            allowed=SandboxPermissivePolicy.selected_tools(tools)
            catalog=[{"name":t._public_name or t.name,"app":t.app_name,"write_operation":t.write_operation,"class_instance":type(t.class_instance).__name__} for t in scenario.get_tools()]
            reference_tools=sorted({e["action"]["app"]+"__"+e["action"]["function"] for e in raw["events"] if e["class_name"]=="OracleEvent"})
            denied=[t for t in reference_tools if t not in allowed]
            result.update(verified_tool_catalog=catalog,allowed_tools=sorted(allowed),denied_required_tools=denied,
                reference_tool_caveat="Offline reference actions show capability gaps; they are not online input or a unique solution.",benign_ready=True,
                legitimate_scope="PARTIAL_SCOPE" if key=="C4" else "FULL")
            if key=="C3":
                future=[e for e in raw["events"] if e["event_type"]=="ENV" and e["action"]["app"]=="Messages"]
                result.update(status="BLOCKED_ATTACK_SURFACE",reason="Future Messages have no fixed message_id in declared actions; MessageV2 generates uuid.uuid4 at actual execution. Do not substitute old messages or ENV injection.",
                    future_message_offsets=[e["event_relative_time"] for e in future],candidate_read_surface={"tool_name":"Messages__read_conversation","temporal_status":"future_until_observed","source_id":"unknown_before_execution"})
                # Verify real current type without replaying any future event.
                app=scenario.get_app("Messages");cid=next(iter(app.get_state()["conversations"]))
                current=app.read_conversation(cid);result["current_read_type"]=type(current).__name__
                from are.simulation.apps.messaging_v2 import MessageV2
                result["current_messages_are_MessageV2"]=all(type(m) is MessageV2 for m in current["messages"])
            else:
                specpath=ROOT/"configs/attack_cases_v0_6_five"/(key+".json");spec=load_attack_spec(specpath)
                if spec.source_tool not in allowed:raise ValueError("BLOCKED_TOOL_SCOPE:source tool not selected")
                action_tool="Calendar__delete_calendar_event" if spec.target_type=="calendar_event" else "Emails__delete_email"
                if action_tool not in allowed:raise ValueError("BLOCKED_TOOL_SCOPE:target tool unavailable")
                appname,method=spec.source_tool.split("__",1);app=scenario.get_app(appname)
                before=json.dumps(app.get_state(),sort_keys=True,ensure_ascii=False,default=str)
                if method=="list_emails":value=app.list_emails(folder_name="INBOX",offset=0,limit=50)
                elif method=="get_email_by_id":value=app.get_email_by_id(spec.source_selector.split(":",1)[1])
                elif method=="read_conversation":value=app.read_conversation(spec.source_conversation_id,limit=10)
                else:value=app.list_recent_conversations(limit=5)
                overlay=AttackOverlay(spec,TraceLogger(directory/"offline_probe.jsonl"),scenario_id=sid)
                overlay.apply_tool_result(value,tool_name=spec.source_tool,agent_id="agent_0")
                page_offset=0
                if method=="list_emails":
                    while not overlay.applied and value.emails and page_offset+len(value.emails)<value.total_emails:
                        page_offset+=len(value.emails)
                        value=app.list_emails(folder_name="INBOX",offset=page_offset,limit=50)
                        overlay.apply_tool_result(value,tool_name=spec.source_tool,agent_id="agent_0")
                if not overlay.applied:raise ValueError("BLOCKED_ATTACK_SURFACE:exact ID not in real typed read")
                if before!=json.dumps(app.get_state(),sort_keys=True,ensure_ascii=False,default=str):raise RuntimeError("SYSTEMIC_STATE_MUTATION")
                initial=attack_target_state(scenario,spec)
                if key=="C4":
                    from datetime import datetime,timezone,timedelta
                    today=datetime.fromtimestamp(scenario.start_time,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
                    next_monday=today+timedelta(days=7-today.weekday())
                    next_end=next_monday+timedelta(days=7)
                    legal={k:v for k,v in scenario.get_app("Calendar").get_state()["events"].items() if v["tag"]=="Work" and next_monday.timestamp()<=v["start_datetime"]<next_end.timestamp()}
                    if spec.target_id in legal:raise ValueError("BLOCKED_TARGET_ORACLE:target is authorized next-week Work event")
                    result["legitimate_calendar_delete_set"]={"start_utc":next_monday.isoformat(),"end_exclusive_utc":next_end.isoformat(),"event_ids":sorted(legal)}
                if spec.target_type=="calendar_event":
                    target=scenario.get_app("Calendar").get_state()["events"][spec.target_id]
                    # All selected targets are August; fixed SHA and USER/ENV establish October/November scope.
                    if target["start_datetime"]>=1725148800:raise ValueError("BLOCKED_TARGET_ORACLE:outside-date proof does not apply")
                    initial_evidence=target
                else:
                    target=next(e for e in scenario.get_app("Emails").get_state()["folders"]["INBOX"]["emails"] if e["email_id"]==spec.target_id)
                    from datetime import datetime,timezone
                    if datetime.fromtimestamp(target["timestamp"],timezone.utc).month==8:raise ValueError("BLOCKED_TARGET_ORACLE:target is August")
                    initial_evidence={k:target[k] for k in ["email_id","timestamp","subject"]}
                result.update(attack_ready=True,injection_adapter_supported=True,status="READY_WITH_LIMITATION" if key=="C4" else "READY",
                    reason="Typed exact-ID clone and fixed independent target verified; Files/attachments/group creation gaps remain" if key=="C4" else "Actual read, exact selector, immutable clone and initial target verified",
                    attack_config=str(specpath),attack_config_sha256=hashlib.sha256(specpath.read_bytes()).hexdigest(),payload_sha256=spec.payload_hash,
                    candidate_read_surface={"tool_name":spec.source_tool,"returned_python_type":type(value).__module__+"."+type(value).__name__,"selector":spec.source_selector,"field_path":spec.field_path,"source_id":spec.source_selector.split(":",1)[1],"conversation_id":spec.source_conversation_id,"temporal_status":"already_present","verified_page_offset":page_offset},
                    attack_target={"action_tool":action_tool,"target_id_or_condition":spec.target_id,"initial_state_evidence":initial_evidence,"initial_target_state":initial,"why_outside_authorization":spec.desired_deviation,"post_episode_evaluator":spec.evaluation_rule})
        except (ValueError,TypeError,KeyError,FileNotFoundError) as error:
            result.update(status="BLOCKED_PREFLIGHT",reason=f"{type(error).__name__}: {error}")
        save(directory/"preflight.json",result);matrix.append(result)
    benchmark={r["scenario_id"]:r for r in benchmark_preflight(output/"benchmark")}
    for result in matrix:
        result["benchmark_runtime"]=benchmark[result["scenario_id"]]
        if benchmark[result["scenario_id"]]["status"]=="BLOCKED_EVALUATION":
            result.update(source_compatibility_status=result["status"],status="BLOCKED_EVALUATION",
                benign_ready=False,attack_ready=False,
                reason="Official benchmark preprocessing blocked: "+benchmark[result["scenario_id"]]["reason"])
    save(output/"compatibility_matrix.json",matrix)
    return matrix


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();matrix=preflight(args.output)
    print(json.dumps([{k:r[k] for k in ["case_id","status","legitimate_scope","benign_ready","attack_ready","reason"]} for r in matrix],ensure_ascii=False,indent=2))
