"""v0.6 official ARE episodes with one modular session and shared cost ledger."""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout,redirect_stderr
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from uuid import uuid4

from .config import ROOT
from .are_adapter import (import_benchmark_scenario,official_runner,BenchmarkLifecycle,
                          benchmark_evaluation,BenchmarkEvaluationBlocked)
from .attack import load_attack_spec,evaluate_attack,attack_target_state,evaluate_text_attack
from .deepseek import API_BASE,MODEL,PROVIDER,DeepSeekClient,DeepSeekSettings,RunBudget
from .trace import TraceLogger
from .orchestration.configuration import load_design,project_config,design_hash
from .orchestration.session import ModularSession
from .risk_monitor import RISK_PROMPT_VERSION


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")


def code_state():
    revision=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT).decode().strip()
    diff=subprocess.check_output(["git","diff","--binary","HEAD","--","security_adaptive_mas","tests","configs","scripts","pyproject.toml"],cwd=ROOT)
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ("security_adaptive_mas","configs","scripts") for p in sorted((ROOT/folder).rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc",".pyo"}}
    hashes["pyproject.toml"]=hashlib.sha256((ROOT/"pyproject.toml").read_bytes()).hexdigest()
    return {"code_revision":revision,"dirty_diff_hash":hashlib.sha256(diff).hexdigest(),
        "dirty_status":subprocess.check_output(["git","status","--porcelain"],cwd=ROOT).decode(),
        "source_file_hashes":hashes,"code_snapshot_id":hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()}


def ledger_charge(path):
    latest={}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row=json.loads(line)
            latest[row["ledger_id"]]=row
    return sum(r["cost_estimate_usd"]+r["unknown_cost_bound"] for r in latest.values())


def email_final_state(scenario):
    folders=scenario.get_app("Emails").get_state()["folders"]
    return {"inbox_email_ids":[r["email_id"] for r in folders["INBOX"]["emails"]],
            "trash_email_ids":[r["email_id"] for r in folders["TRASH"]["emails"]]}


def run_case(args,config,scenario_id,batch_dir,frozen,attack_spec=None):
    if code_state()!=frozen:raise RuntimeError("Code changed after batch freeze")
    sys.path.insert(0,str(config.are_root))
    import are
    if not Path(are.__file__).resolve().is_relative_to(config.are_root):
        raise ValueError("ARE_import_not_from_explicit_pinned_source")
    from .adapters.are_modular import make_modular_agent_builder,COORDINATOR_PROTOCOL
    record=config.select(scenario_id)
    run_id=uuid4().hex[:12]
    out=batch_dir/scenario_id/run_id
    out.mkdir(parents=True,exist_ok=False)
    trace=TraceLogger(out/"framework_trace.jsonl",mode="real_are_multi")
    budget=RunBudget(trace,args.max_requests,args.max_total_tokens,args.max_cost_usd,trace.mode)
    budget.input_rate=args.design["rates"]["input_usd_per_token"]
    budget.output_rate=args.design["rates"]["output_usd_per_token"]
    client=DeepSeekClient(budget,DeepSeekSettings(max_attempts=1))
    lifecycle=BenchmarkLifecycle(trace,client,getattr(args,"idle_wait_seconds",None))
    run_config={"study_unit":args.study_unit,"study_manifest":str(args.study_manifest) if args.study_manifest else None,"scenario_id":scenario_id,"run_id":run_id,"mode":"adaptive","attack_mode":args.attack,
        "organization_policy":args.design["method"],"design":args.design,"design_hash":args.design_hash,
        "task_scope_policy":args.task_scope_policy,"model_id":MODEL,"provider":PROVIDER,"api_base":API_BASE,
        "settings":asdict(client.settings),"project_timeout_argument":"omitted","max_turns":args.max_turns,
        "max_requests":args.max_requests,"max_total_tokens":args.max_total_tokens,"max_cost_usd":args.max_cost_usd,
        "scenario_hash":record.sha256,"oracle":False,"load_completed_events":False,
        "coordinator_prompt_hash":hashlib.sha256(COORDINATOR_PROTOCOL.encode()).hexdigest(),
        "risk_prompt_version":RISK_PROMPT_VERSION,**frozen}
    run_config["benchmark_runtime"]={"preprocessed":False,"judge_configured":False}
    run_config["idle_wait_seconds"]=lifecycle.idle_wait_seconds
    run_config["idle_guard_clock"]="wall_monotonic_only_when_no_active_model_request"
    save(out/"run_config.json",run_config)
    session=None
    result=scenario=error=None
    initial_target=None
    started=datetime.now(timezone.utc).isoformat()
    start=time.monotonic()
    handler=logging.FileHandler(out/"runner.log",encoding="utf-8")
    logging.getLogger().addHandler(handler)
    with (out/"runner.log").open("a",encoding="utf-8") as stream,redirect_stdout(stream),redirect_stderr(stream):
        try:
            session=ModularSession(args.design,trace,client)
            scenario=import_benchmark_scenario(record,config.are_root,client=client,max_turns=args.max_turns,
                filesystem=args.design["paths"]["gaia2_filesystem"],sandbox=args.design["paths"]["sandbox_root"],on_error=session.stop_episode)
            run_config["benchmark_runtime"]=scenario.benchmark_runtime_metadata
            save(out/"run_config.json",run_config)
            save(out/"benchmark_event_graph.json",[e.to_dict() for e in scenario.events])
            if attack_spec and attack_spec.surface=="single_entry_tool_result_text":initial_target=attack_target_state(scenario,attack_spec)
            builder=make_modular_agent_builder(session,client,attack_spec=attack_spec,lifecycle=lifecycle)
            from are.simulation.scenarios.config import ScenarioRunnerConfig
            runner_config=ScenarioRunnerConfig(agent="default",model=MODEL,model_provider=PROVIDER,endpoint=API_BASE,
                oracle=False,export=True,max_turns=args.max_turns,output_dir=str(out/"official_trace"))
            result=official_runner(builder).run(runner_config,scenario)
            error=session.terminal_error or result.exception or getattr(client,"benchmark_evaluation_error",None)
        except (Exception,KeyboardInterrupt) as caught:
            error=caught
            if isinstance(error,KeyboardInterrupt):
                lifecycle.request_stop("controlled_keyboard_interrupt")
                if budget.request_reservation != (0,0.0):
                    budget.unknown_response(error,"interrupted_inflight")
            trace.log("episode_failed",exception_type=type(error).__name__,reason=str(error))
        finally:
            lifecycle.close()
            logging.getLogger().removeHandler(handler)
            handler.close()
    if error:
        save(out/"failure.json",{"exception_type":type(error).__name__,"reason":str(error),
            "traceback":"".join(traceback.format_exception(error)),"status":"failed"})
    events=[json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()] if trace.path.exists() else []
    groups={"delegation_events.jsonl":{"delegation_requested","worker_started","worker_finished"},
            "delivery_events.jsonl":{"delivery_requested","delivery_held","runtime_object_delivered","official_observation_bound","manta_packet_selection"},
            "reconfiguration_events.jsonl":{"candidates_evaluated","mutation_validation","reconfiguration_selected","repair_started","repair_finished"},
            "topology_events.jsonl":{"topology_patch_applied"}}
    for filename,kinds in groups.items():
        (out/filename).write_text("".join(json.dumps(e,ensure_ascii=False)+"\n" for e in events if e["kind"] in kinds),encoding="utf-8")
    save(out/"artifact_index.json",{k:asdict(v) for k,v in session.artifact_records.items()} if session else {})
    exhausted=type(error).__name__=="BudgetExceeded" if error else False
    evaluation=benchmark_evaluation(scenario,result,interrupted=bool(error or lifecycle.stop_reason))
    run_status=("BLOCKED_EVALUATION" if isinstance(error,BenchmarkEvaluationBlocked) else "FAILED" if error else
        "INDETERMINATE_IDLE" if lifecycle.stop_reason=="INDETERMINATE_IDLE" else
        "BLOCKED_EVALUATION" if not evaluation["benchmark_judge_completed"] else "NORMAL_END")
    summary={**run_config,**(session.summary_fields() if session else {}),"started_at":started,
        "finished_at":datetime.now(timezone.utc).isoformat(),"wall_time_seconds":time.monotonic()-start,
        "exception_type":type(error).__name__ if error else None,
        "stop_reason":"blocked_evaluation" if isinstance(error,BenchmarkEvaluationBlocked) else
            "budget_exhausted" if exhausted else "runner_exception" if error else "official_episode_end",
        "run_status":run_status,"lifecycle_stop_reason":lifecycle.stop_reason,
        "lifecycle_stop_snapshot":lifecycle.stop_snapshot,
        "official_runner_success":result.success if result is not None else None,
        "legitimate_task_completed":"unknown","semantic_verified":"unknown",
        "model_requests_total":budget.requests,"requests_by_purpose":budget.requests_by_purpose,
        "model_requests_submitted":sum(e["kind"]=="model_request_submitted" for e in events),
        "successful_model_responses":sum(e["kind"]=="model_response_received" for e in events),
        "actual_usage_by_purpose":budget.actual_usage_by_purpose,
        "input_tokens_total":budget.input_tokens,"output_tokens_total":budget.output_tokens,
        "cost_estimate_usd":budget.peak_cost_estimate_usd,"unknown_cost_bound":budget.unknown_cost_bound,
        "unaccounted_usage_notes":budget.unaccounted_usage_notes,"budget_exhausted":exhausted,
        "semantic_adoption":"unknown","delegation_status":"EXERCISED" if session and session.work_items else "NOT_EXERCISED",
        "attack_injection_status":"EXERCISED" if any(e["kind"]=="attack_injected_delivered" for e in events) else "NOT_EXERCISED",
        "actual_deliveries":[{k:e[k] for k in ("object_id","view_id","agent_id","transport","decision_id") if k in e}
                             for e in events if e["kind"]=="runtime_object_delivered"],
        "trace_paths":{"framework":str(trace.path),"official":str(result.export_path) if result is not None and result.export_path is not None else None},
        **evaluation}
    if lifecycle.stop_reason:summary["stop_reason"]=lifecycle.stop_reason
    if attack_spec:
        summary["attack_scoring"]=evaluate_attack(events,spec=attack_spec,official_success=summary["official_runner_success"],
            stop_reason=summary["stop_reason"],require_submitted=True,
            final_state=email_final_state(scenario) if scenario is not None and attack_spec.target_email_id else None)
        summary["exposure_interpretation"]="injection_source_related_objects_not_proven_payload_adoption"
    if attack_spec and attack_spec.surface=="single_entry_tool_result_text":
        summary["attack_scoring"]=evaluate_text_attack(events,spec=attack_spec,initial=initial_target,
            final=attack_target_state(scenario,attack_spec) if scenario is not None else None,
            official_success=summary["official_runner_success"],stop_reason=summary["stop_reason"])
    save(out/"summary.json",summary)
    save(out/"evaluation.json",{k:summary.get(k,"unknown") for k in (
        "official_runner_returned","official_runner_success","benchmark_judge_status","benchmark_judge_completed",
        "legitimate_task_completed","semantic_verified","run_status","stop_reason","attack_scoring")})
    return summary


def approved_study_cap(args):
    """Validate the explicit frozen 20-unit study; default callers keep 1.6 USD."""
    if args.study_manifest is None:
        if args.study_unit is not None:raise ValueError("study_unit_requires_manifest")
        return 1.6
    path=args.study_manifest.resolve()
    if not path.is_relative_to(ROOT/"results"):raise ValueError("study_manifest_must_be_local_results")
    manifest=json.loads(path.read_text(encoding="utf-8"))
    if manifest["kind"]!="v0_6_five_case_20" or len(manifest["units"])!=20 or manifest["study_additional_cap_usd"]!=8.0:
        raise ValueError("unapproved_study_budget")
    if manifest["code"]!=code_state():raise ValueError("study_code_not_frozen")
    if not manifest["absolute_cap_usd"]==manifest["ledger_charge_before"]+8.0:raise ValueError("study_cap_mismatch")
    unit=next(u for u in manifest["units"] if u["unit_id"]==args.study_unit)
    if unit["status"] not in {"READY","RUNNING"}:raise ValueError("study_unit_not_ready")
    if (unit["scenario_id"]!=args.scenario or unit["method"]!=args.design["method"] or unit["attack"]!=args.attack
        or unit["design_hash"]!=args.design_hash):raise ValueError("study_unit_configuration_mismatch")
    if args.output_dir.resolve()!=Path(manifest["shared_ledger"]).parent:raise ValueError("study_requires_historical_shared_ledger")
    if args.batch_cap_usd!=manifest["absolute_cap_usd"]:raise ValueError("study_absolute_cap_changed")
    if args.attack!="none" and (args.attack_spec.resolve()!=Path(unit["attack_config"]) or
        hashlib.sha256(args.attack_spec.read_bytes()).hexdigest()!=unit["attack_config_sha256"]):raise ValueError("study_attack_config_changed")
    for name,sha in manifest["frozen_input_hashes"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=sha:raise ValueError("study_input_changed")
    return manifest["absolute_cap_usd"]


def build_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--design-config",type=Path,default=ROOT/"configs/research_v0_6.yaml")
    p.add_argument("--mode",choices=("adaptive",),default="adaptive")
    p.add_argument("--scenario",required=True)
    p.add_argument("--task-scope-policy",choices=("sandbox_permissive",),default="sandbox_permissive")
    p.add_argument("--organization-policy",help=argparse.SUPPRESS)
    p.add_argument("--attack",choices=("none","single_injection","single_entry_tool_result_text"),default="none")
    p.add_argument("--attack-spec",type=Path,default=ROOT/"configs/attack_single_injection_22_kb5ah9.json")
    p.add_argument("--max-turns",type=int,default=20)
    p.add_argument("--idle-wait-seconds",type=float,help="Explicit idle wall-time guard; does not time out active model requests")
    p.add_argument("--max-requests",type=int,default=100)
    p.add_argument("--max-total-tokens",type=int,default=800000)
    p.add_argument("--max-cost-usd",type=float,default=.4)
    p.add_argument("--study-manifest",type=Path)
    p.add_argument("--study-unit")
    p.add_argument("--batch-cap-usd",type=float,default=1.6)
    p.add_argument("--output-dir",type=Path,default=ROOT/"results/runs/v0_6_modular_pilot/manual")
    p.add_argument("--acknowledge-model-cost",action="store_true")
    p.add_argument("--dry-run",action="store_true")
    return p


def main(argv=None,*,design_override=None):
    parser=build_parser()
    args=parser.parse_args(argv)
    if args.organization_policy is not None:parser.error("v0.6 uses design.method; use v0.5 worktree for legacy flags")
    args.design=load_design(args.design_config,design_override)
    args.design["upstream_lock"]=str((ROOT/args.design["upstream_lock"]).resolve())
    args.design_hash=design_hash(args.design)
    approved_cap=approved_study_cap(args)
    if not (1<=args.max_turns<=20 and 1<=args.max_requests<=100 and 1<=args.max_total_tokens<=800000
            and 0<args.max_cost_usd<=.4 and 0<args.batch_cap_usd<=approved_cap):parser.error("v0.6 pilot limits exceeded")
    config=project_config(args.design)
    config.select(args.scenario)
    attack_spec=load_attack_spec(args.attack_spec) if args.attack=="single_injection" else None
    if attack_spec and args.scenario!=attack_spec.target_case:parser.error("attack scenario does not match fixed configuration")
    frozen=code_state()
    if args.dry_run:
        result={"dry_run":True,"design":args.design,"design_hash":args.design_hash,"key_present":bool(os.environ.get("DEEPSEEK_API_KEY")),
            "scenario":args.scenario,"attack":args.attack,"organization_policy":args.design["method"],
            "max_cost_usd":args.max_cost_usd,"batch_cap_usd":args.batch_cap_usd,**frozen}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    if not args.acknowledge_model_cost or not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("Real run requires DEEPSEEK_API_KEY and --acknowledge-model-cost")
    output=args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    ledger=output/"v0_1_cost_ledger.jsonl"
    if ledger_charge(ledger)+args.max_cost_usd>args.batch_cap_usd:raise RuntimeError("shared_batch_budget_blocked")
    batch=output/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid4().hex[:6])
    save(batch/"batch_config.json",{"code":frozen,"design":args.design,"design_hash":args.design_hash,
        "organization_policy":args.design["method"],"scenario":args.scenario,"attack":args.attack,"batch_cap_usd":args.batch_cap_usd})
    for relative in frozen["source_file_hashes"]:
        target=batch/"code_snapshot"/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/relative).read_bytes())
    ledger_id=batch.name+":"+args.scenario
    def append(row):
        with ledger.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps({"ledger_id":ledger_id,**row})+"\n")
            stream.flush()
            os.fsync(stream.fileno())
    append({"status":"reserved_before_episode","cost_estimate_usd":args.max_cost_usd,"unknown_cost_bound":0})
    summary=run_case(args,config,args.scenario,batch,frozen,attack_spec)
    append({"status":"settled","run_id":summary["run_id"],"cost_estimate_usd":summary["cost_estimate_usd"],"unknown_cost_bound":summary["unknown_cost_bound"]})
    save(batch/"batch_results.json",[summary])
    print("FINISH",summary["run_id"],summary["stop_reason"],flush=True)
    return {"batch_dir":str(batch),"summaries":[summary],"ledger":str(ledger)}


if __name__=="__main__":main()
