"""Read existing model_io/traces; emit lengths and usage, never duplicate prompt bodies."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path

from security_adaptive_mas.run_framework import save,ledger_charge


def analyze_episode(directory,*,interrupted_record=None):
    directory=Path(directory);events=[json.loads(s) for s in (directory/"framework_trace.jsonl").read_text(encoding="utf-8").splitlines()]
    if interrupted_record is None:
        summary=json.loads((directory/"summary.json").read_text(encoding="utf-8"))
        origin="completed_runner_summary"
    else:
        # Explicit partial evidence mode, never write or fabricate a Runner summary/export.
        config=json.loads((directory/"run_config.json").read_text(encoding="utf-8"))
        groups={}
        for event in events:
            if event["kind"]!="model_usage":continue
            item=groups.setdefault(event["purpose"],{"requests":0,"input_tokens":0,"output_tokens":0,"cost_estimate_usd":0.0,"latency_seconds":0.0})
            item["requests"]+=1;item["input_tokens"]+=event["prompt_tokens"];item["output_tokens"]+=event["completion_tokens"]
            item["latency_seconds"]+=event["latency_seconds"]
            item["cost_estimate_usd"]+=event["prompt_tokens"]*config["design"]["rates"]["input_usd_per_token"]+event["completion_tokens"]*config["design"]["rates"]["output_usd_per_token"]
        counts=Counter(e["kind"] for e in events)
        summary={**config,"actual_usage_by_purpose":groups,"exception_type":"unknown_no_runner_return",
            "stop_reason":"operator_stop_systemic_ARE_notification_wait","official_runner_success":"unknown_no_official_export",
            "legitimate_task_completed":"unknown","model_requests_submitted":counts["model_request_submitted"],
            "successful_model_responses":counts["model_response_received"],
            "requests_by_purpose":dict(Counter(e["purpose"] for e in events if e["kind"]=="model_request_submitted")),
            "input_tokens_total":interrupted_record["actual_input_tokens"],"output_tokens_total":interrupted_record["actual_output_tokens"],
            "cost_estimate_usd":interrupted_record["known_cost_estimate_usd"],"unknown_cost_bound":"unknown_core_summary_missing",
            "wall_time_seconds":"unknown_core_summary_missing","distinct_agent_instances":sorted({"agent_0"}|{e["instance_id"] for e in events if e["kind"]=="worker_started"}),
            "worker_steps":0 if counts["worker_started"]==0 else "unknown_interrupted",
            "effective_graph_patches":sum(e["graph_version"]!=e["previous_version"] for e in events if e["kind"]=="topology_patch_applied")}
        origin="partial_trace_after_explicit_operator_stop"

    submitted={e["request_id"].split(":")[0]:e for e in events if e["kind"]=="model_request_submitted"}
    received={e["request_id"]:e for e in events if e["kind"]=="model_response_received"}
    rows=[];seen_requests=set();seen_refs=defaultdict(set)
    prepared=[e for e in events if e["kind"]=="model_input_prepared"]
    for step,event in enumerate(prepared,1):
        pid=event["prepared_id"];request=json.loads((directory/"model_io"/(pid+"_request.json")).read_text(encoding="utf-8"))
        sent=submitted.get(pid);rid=sent["request_id"] if sent else None
        response_file=directory/"model_io"/(pid+"_0_response.json")
        response=json.loads(response_file.read_text(encoding="utf-8")) if response_file.exists() else None
        usage=response.get("usage") if response else None
        roles=Counter()
        for message in request["messages"]:
            content=message.get("content")
            text=content if isinstance(content,str) else json.dumps(content,ensure_ascii=False)
            roles[message["role"]]+=len(text.encode("utf-8"))
        raw=json.dumps({k:request[k] for k in ["messages","tools","response_format"]},ensure_ascii=False).encode()
        digest=hashlib.sha256(raw).hexdigest();actor=request["instance_id"]
        refs=[];repeat_known_bytes=0;unknown_repeated=0
        for comp in request.get("components") or []:
            if "object_id" not in comp:continue
            ref=(comp["object_id"],comp.get("version"),comp.get("view_id"))
            repeated=ref in seen_refs[actor];seen_refs[actor].add(ref)
            if repeated:
                if "rendered_text" in comp:repeat_known_bytes+=len(comp["rendered_text"].encode("utf-8"))
                else:unknown_repeated+=1
            refs.append({"object_id":ref[0],"version":ref[1],"view":ref[2],"repeated_to_actor":repeated})
        time=event["time_utc"]
        graph_version=1
        for patch in events:
            if patch["kind"]=="topology_patch_applied" and patch["time_utc"]<=time:graph_version=patch["graph_version"]
        inp=usage.get("prompt_tokens") if usage else None;out=usage.get("completion_tokens") if usage else None
        rows.append({"episode_id":summary["run_id"],"request_id":rid,"prepared_id":pid,"step":step,"actor":actor,
            "purpose":request["purpose"],"graph_version":graph_version,"prepared":True,"submitted":sent is not None,
            "response":rid in received,"actual_input_tokens":inp if inp is not None else "unknown",
            "actual_output_tokens":out if out is not None else "unknown","wall_time_seconds":received[rid]["elapsed_seconds"] if rid in received else "unknown",
            "cost_estimate_usd":inp*summary["design"]["rates"]["input_usd_per_token"]+out*summary["design"]["rates"]["output_usd_per_token"] if inp is not None and out is not None else "unknown",
            "role_text_utf8_bytes":dict(roles),"serialized_utf8_bytes_proxy":len(raw),"tools_schema_utf8_bytes":len(json.dumps(request["tools"],ensure_ascii=False).encode()),
            "artifact_refs":refs,"repeated_identified_rendered_bytes":repeat_known_bytes,"repeated_refs_unknown_byte_length":unknown_repeated,
            "exact_duplicate_input":digest in seen_requests,"official_history_attribution":"unknown","bytes_are_tokens":False})
        seen_requests.add(digest)
    save(directory/"context_growth.json",{"analysis_origin":origin,"requests":rows,"budget_preflight":[e for e in events if e["kind"]=="preflight_budget_rejection"],"per_completed_subgoal_cost":"unknown_no_independent_checker"})
    save(directory/"model_usage_by_purpose.json",summary["actual_usage_by_purpose"])
    save(directory/"attack_exposure_trace.json",{"score":summary.get("attack_scoring","NOT_APPLICABLE"),"events":[{k:v for k,v in e.items() if k not in {"original_text","injected_text"}} for e in events if e["kind"] in {"attack_injected","attack_injected_delivered","attack_surface_not_hit"}]})
    counts=Counter(e["kind"] for e in events)
    return {"analysis_origin":origin,"run_id":summary["run_id"],"exception_type":summary["exception_type"],"stop_reason":summary["stop_reason"],
        "official_runner_success":summary["official_runner_success"],"legitimate_task_completed":summary["legitimate_task_completed"],
        "requests":summary["model_requests_submitted"],"responses":summary["successful_model_responses"],
        "requests_by_purpose":summary["requests_by_purpose"],"average_actual_input_tokens":summary["input_tokens_total"]/summary["successful_model_responses"] if summary["successful_model_responses"] else "unknown",
        "input_tokens":summary["input_tokens_total"],"output_tokens":summary["output_tokens_total"],"cost_estimate_usd":summary["cost_estimate_usd"],"unknown_cost_bound":summary["unknown_cost_bound"],"wall_time_seconds":summary["wall_time_seconds"],
        "instances":summary["distinct_agent_instances"],"worker_steps":summary["worker_steps"],"graph_patches":summary["effective_graph_patches"],
        "event_counts":dict(counts),"tools":dict(Counter(e["tool"] for e in events if e["kind"]=="tool_started")),
        "risk_results":dict(Counter(e["suspicion"] for e in events if e["kind"]=="runtime_risk_observation")),
        "attack_scoring":summary.get("attack_scoring","NOT_APPLICABLE"),"actual_usage_by_purpose":summary["actual_usage_by_purpose"]}


def analyze_study(directory):
    directory=Path(directory);manifest=json.loads((directory/"study_manifest.json").read_text(encoding="utf-8"));matrix=[]
    for unit in manifest["units"]:
        row={k:unit[k] for k in ["unit_id","scenario_id","method","attack","status","legitimate_scope"]}
        if unit.get("run_dir"):
            interruption=json.loads(Path(unit["operator_stop"]).read_text(encoding="utf-8")) if unit.get("operator_stop") else None
            row.update(analyze_episode(unit["run_dir"],interrupted_record=interruption))
        else:row.update(reason=unit.get("reason",unit.get("blocked_by",unit.get("readiness_reason"))),requests="NOT_RUN",cost_estimate_usd="NOT_RUN")
        matrix.append(row)
    save(directory/"results_matrix.json",matrix)
    charge=ledger_charge(Path(manifest["shared_ledger"]))
    save(directory/"study_cost_ledger_snapshot.json",{"ledger_charge_before":manifest["ledger_charge_before"],"ledger_charge_after":charge,"new_charge_including_unknown":charge-manifest["ledger_charge_before"],"additional_cap":8.0,"absolute_cap":manifest["absolute_cap_usd"]})
    return matrix


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--study-dir",type=Path,required=True)
    args=parser.parse_args();print(json.dumps(analyze_study(args.study_dir),ensure_ascii=False,indent=2))
