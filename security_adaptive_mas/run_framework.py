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
from .attacks.loader import load_for_run
from .attacks.runtime import AttackRuntime
from .deepseek import API_BASE,MODEL,PROVIDER,DeepSeekClient,DeepSeekSettings,RunBudget
from .trace import TraceLogger
from .orchestration.configuration import load_design,project_config,design_hash
from .orchestration.session import ModularSession
from .risk_monitor import RISK_PROMPT_VERSION

# Explicit authorization for the 2026-10-15 C1_B1 validation only.
C1_LEDGER = Path("D:/SecurityAdaptiveMAS-v0.6/results/runs/v0_6_modular_pilot/20261015_01/v0_1_cost_ledger.jsonl")
C1_STUDY_CAP = .7909167 + 8.0


def c1_authorization(args):
    path = getattr(args, "c1_b1_authorization", None)
    if path is None:
        return None
    path = path.resolve()
    if not path.is_relative_to(ROOT / "results"):
        raise ValueError("c1_authorization_must_be_local_results")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    actual = {"scenario": args.scenario, "method": args.design["method"], "attack": args.attack,
        "model": args.design["model"], "max_turns": args.max_turns, "max_requests": args.max_requests,
        "max_total_tokens": args.max_total_tokens, "max_cost_usd": args.max_cost_usd,
        "idle_wait_seconds": args.idle_wait_seconds}
    expected = {"scenario": "scenario_universe_22_0421h2", "method": "modular_risk_aware",
        "attack": "none", "model": "deepseek-flash", "max_turns": 20, "max_requests": 200,
        "max_total_tokens": 2000000, "max_cost_usd": 2.0, "idle_wait_seconds": None}
    if (manifest["kind"] != "v0_6_C1_B1_single_validation" or manifest["episode_limit"] != 1
        or actual != expected or manifest["configuration"] != expected
        or args.study_manifest is not None or args.study_unit is not None):
        raise ValueError("c1_authorization_scope_mismatch")
    if manifest["code"] != code_state() or manifest["design_hash"] != args.design_hash:
        raise ValueError("c1_authorization_snapshot_changed")
    if (Path(manifest["shared_ledger"]).resolve() != C1_LEDGER.resolve()
        or args.output_dir.resolve() != C1_LEDGER.resolve().parent
        or args.batch_cap_usd != C1_STUDY_CAP or manifest["absolute_cap_usd"] != C1_STUDY_CAP):
        raise ValueError("c1_authorization_shared_budget_mismatch")
    if hashlib.sha256(C1_LEDGER.read_bytes()).hexdigest() != manifest["ledger_sha256_before"]:
        raise ValueError("c1_authorization_ledger_changed")
    for name, sha in manifest["frozen_input_hashes"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != sha:
            raise ValueError("c1_authorization_input_changed")
    # A fixed claim path also prevents reuse through a second manifest filename.
    claim = C1_LEDGER.parent / "C1_B1_20261015_2usd.claim.json"
    if claim.exists():
        raise ValueError("c1_authorization_already_attempted")
    return {"manifest": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "claim": str(claim)}


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
    run_config["attack_configuration"]=None if attack_spec is None else asdict(attack_spec)
    run_config["single_episode_authorization"]=getattr(args,"validated_c1_authorization",None)
    if args.design['version']=='v0.7':
        run_config['handoff_study_authorization']=getattr(args,'validated_handoff_authorization',None)
    run_config["idle_wait_seconds"]=lifecycle.idle_wait_seconds
    run_config["idle_guard_clock"]="wall_monotonic_only_when_no_active_model_request"
    save(out/"run_config.json",run_config)
    session=attack_runtime=None
    result=scenario=error=None
    initial_target=None
    started=datetime.now(timezone.utc).isoformat()
    start=time.monotonic()
    handler=logging.FileHandler(out/"runner.log",encoding="utf-8")
    logging.getLogger().addHandler(handler)
    with (out/"runner.log").open("a",encoding="utf-8") as stream,redirect_stdout(stream),redirect_stderr(stream):
        try:
            if attack_spec is not None:
                attack_runtime=AttackRuntime(attack_spec,trace,scenario_id=scenario_id)
            session=ModularSession(args.design,trace,client)
            scenario=import_benchmark_scenario(record,config.are_root,client=client,max_turns=args.max_turns,
                filesystem=args.design["paths"]["gaia2_filesystem"],sandbox=args.design["paths"]["sandbox_root"],on_error=session.stop_episode)
            run_config["benchmark_runtime"]=scenario.benchmark_runtime_metadata
            save(out/"run_config.json",run_config)
            save(out/"benchmark_event_graph.json",[e.to_dict() for e in scenario.events])
            if attack_runtime is not None:initial_target=attack_runtime.snapshot(scenario)
            builder=make_modular_agent_builder(session,client,attack_runtime=attack_runtime,lifecycle=lifecycle)
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
    if args.design['version']=='v0.7' and session is not None and session.runtime is not None:
        trace.log('handoff_graph_snapshot',phase='final',graph=session.graph_snapshot())
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
    if attack_runtime is not None:
        summary["attack_runtime"]=attack_runtime.finish(interrupted=bool(error or lifecycle.stop_reason))
        summary["attack_scoring"]=attack_runtime.evaluate(events,initial=initial_target,
            final=attack_runtime.snapshot(scenario) if scenario is not None else None,
            official_success=summary["official_runner_success"],legitimate_task_completed=summary["legitimate_task_completed"],
            stop_reason=summary["stop_reason"],interrupted=bool(error or lifecycle.stop_reason),model_io_dir=out/"model_io")
        summary["exposure_interpretation"]="actual_submitted_payload_text_is_not_proven_adoption_or_causality"
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


def handoff_plan_hash(manifest):
    keys=('unit_id','case_id','condition','phase','scenario_id','attack','eligibility','configuration_path','design_hash',
        'attack_config','attack_config_sha256','payload_sha256')
    plan={key:manifest[key] for key in ('kind','study_id','code','shared_ledger','ledger_charge_before','episode_limits','frozen_input_hashes')}
    plan['units']=[{key:unit[key] for key in keys} for unit in manifest['units']]
    return hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()


def handoff_authorization(args):
    """New study only: no inheritance from the v0.6 or single-C1 approvals."""
    if args.design.get('version')!='v0.7':
        if args.handoff_authorization is not None or args.shared_ledger is not None:
            raise ValueError('handoff_authorization_requires_v0_7')
        return None
    if args.c1_b1_authorization is not None:
        raise ValueError('v0_7_cannot_reuse_C1_authorization')
    if args.dry_run:
        if args.study_manifest is not None or args.handoff_authorization is not None or args.shared_ledger is not None:
            raise ValueError('v0_7_dry_run_is_unfunded_configuration_check_only')
        return None
    if args.study_manifest is None or args.handoff_authorization is None or args.shared_ledger is None:
        raise ValueError('BLOCKED_v0_7_requires_fresh_study_fee_authorization_and_shared_ledger')
    path=args.study_manifest.resolve()
    if not path.is_relative_to(ROOT/'results/runs/evidence_handoff_v0_7'):
        raise ValueError('v0_7_study_directory_mismatch')
    manifest=json.loads(path.read_text(encoding='utf-8'))
    approval=json.loads(args.handoff_authorization.read_text(encoding='utf-8'))
    if (manifest['kind']!='evidence_handoff_v0_7' or manifest['code']!=code_state() or manifest['code']['dirty_status'] or len(manifest['units'])!=30
        or approval['kind']!='v0_7_fee_authorization' or approval['study_id']!=manifest['study_id']
        or approval['code_snapshot_id']!=manifest['code']['code_snapshot_id']
        or approval['plan_sha256']!=manifest['plan_sha256'] or manifest['plan_sha256']!=handoff_plan_hash(manifest)
        or not isinstance(approval['user_confirmation'],str) or not approval['user_confirmation'].strip()
        or not 0<approval['additional_cap_usd']<=manifest['planned_additional_cap_usd']):
        raise ValueError('v0_7_authorization_or_snapshot_mismatch')
    unit=next(u for u in manifest['units'] if u['unit_id']==args.study_unit)
    from .orchestration.handoff_pilot import CONDITIONS
    if (args.design['experiment']['condition']!=CONDITIONS[unit['unit_id'].split('_')[1]]
        or args.design['experiment']['case_id']!=unit['case_id'] or unit['condition']!=args.design['experiment']['condition']
        or (unit['attack']=='none')!=(unit['unit_id'].endswith('_N0'))):
        raise ValueError('v0_7_condition_or_attack_scope_mismatch')
    if (args.study_unit not in approval['authorized_units'] or unit['eligibility']!='READY' or unit['status'] not in {'NOT_RUN','RUNNING'}
        or unit['design_hash']!=args.design_hash or unit['scenario_id']!=args.scenario or unit['attack']!=args.attack
        or args.output_dir.resolve()!=path.parent/'units'/args.study_unit):
        raise ValueError('v0_7_unit_not_authorized_or_configuration_changed')
    if manifest.get('stop_reason'):
        raise ValueError('v0_7_study_stopped_after_prior_failure')
    if args.study_unit!='C1_A3' and not manifest.get('real_pilot_passed',False):
        raise ValueError('BLOCKED_real_C1_A3_pilot_required_first')
    if unit['phase']==2 and approval.get('phase2_authorized') is not True:
        raise ValueError('BLOCKED_phase2_requires_separate_explicit_authorization')
    limits={'max_turns':args.max_turns,'max_requests':args.max_requests,'max_total_tokens':args.max_total_tokens,
        'max_cost_usd':args.max_cost_usd,'idle_wait_seconds':args.idle_wait_seconds}
    if limits!=manifest['episode_limits'] or args.batch_cap_usd!=manifest['ledger_charge_before']+approval['additional_cap_usd']:
        raise ValueError('v0_7_frozen_limits_or_shared_cap_changed')
    if (args.shared_ledger.resolve()!=Path(manifest['shared_ledger']).resolve()
        or not args.shared_ledger.read_bytes().startswith((path.parent/'inputs/ledger_before.jsonl').read_bytes())):
        raise ValueError('v0_7_historical_ledger_prefix_changed')
    for name,sha in manifest['frozen_input_hashes'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=sha:
            raise ValueError('v0_7_frozen_input_changed')
    if unit['attack']!='none' and args.attack_spec.resolve()!=Path(unit['attack_config']).resolve():
        raise ValueError('v0_7_attack_configuration_changed')
    claim=path.parent/'claims'/(args.study_unit+'.claim.json')
    if claim.exists():
        raise ValueError('v0_7_unit_already_attempted_no_automatic_retry')
    return {'manifest':str(path),'authorization_sha256':hashlib.sha256(args.handoff_authorization.read_bytes()).hexdigest(),
        'claim':str(claim),'absolute_cap_usd':args.batch_cap_usd}


def build_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--design-config",type=Path,default=ROOT/"configs/research_v0_6.yaml")
    p.add_argument("--mode",choices=("adaptive",),default="adaptive")
    p.add_argument("--scenario",required=True)
    p.add_argument("--task-scope-policy",choices=("sandbox_permissive",),default="sandbox_permissive")
    p.add_argument("--organization-policy",help=argparse.SUPPRESS)
    p.add_argument("--attack",default="none",help="none or a mode explicitly supported by the registered attack adapter")
    p.add_argument("--attack-spec",type=Path,default=ROOT/"configs/attack_single_injection_22_kb5ah9.json")
    p.add_argument("--max-turns",type=int,default=20)
    p.add_argument("--idle-wait-seconds",type=float,help="Explicit idle wall-time guard; does not time out active model requests")
    p.add_argument("--max-requests",type=int,default=100)
    p.add_argument("--max-total-tokens",type=int,default=800000)
    p.add_argument("--max-cost-usd",type=float,default=.4)
    p.add_argument("--study-manifest",type=Path)
    p.add_argument("--c1-b1-authorization",type=Path,help="Frozen single-use C1_B1 2 USD authorization; defaults remain unchanged")
    p.add_argument("--study-unit")
    p.add_argument('--handoff-authorization',type=Path,help='Fresh v0.7 user fee approval; never created by prepare')
    p.add_argument('--shared-ledger',type=Path,help='Existing historical ledger, only for a frozen authorized v0.7 study')
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
    args.validated_handoff_authorization=handoff_authorization(args)
    args.validated_c1_authorization=c1_authorization(args)
    v07=args.design.get('version')=='v0.7'
    approved_cap=(args.validated_handoff_authorization['absolute_cap_usd'] if args.validated_handoff_authorization else 1.6) if v07 else (C1_STUDY_CAP if args.validated_c1_authorization else approved_study_cap(args))
    requests_cap,tokens_cap,cost_cap=(160,2000000,.4) if v07 else (200,2000000,2.0) if args.validated_c1_authorization else (100,800000,.4)
    if not (1<=args.max_turns<=20 and 1<=args.max_requests<=requests_cap and 1<=args.max_total_tokens<=tokens_cap
            and 0<args.max_cost_usd<=cost_cap and 0<args.batch_cap_usd<=approved_cap):parser.error("v0.6 pilot limits exceeded")
    config=project_config(args.design)
    config.select(args.scenario)
    attack_spec=load_for_run(args.attack,args.attack_spec,args.scenario)
    frozen=code_state()
    if args.dry_run:
        result={"dry_run":True,"design":args.design,"design_hash":args.design_hash,"key_present":bool(os.environ.get("DEEPSEEK_API_KEY")),
            "scenario":args.scenario,"attack":args.attack,"organization_policy":args.design["method"],
            "max_cost_usd":args.max_cost_usd,"batch_cap_usd":args.batch_cap_usd,
            "max_requests":args.max_requests,"max_total_tokens":args.max_total_tokens,
            "attack_configuration":None if attack_spec is None else {"attack_id":attack_spec.attack_id,
                "surface_type":attack_spec.surface_type,"payload_sha256":attack_spec.payload_sha256,
                "evaluator_id":attack_spec.evaluator_id},
            "single_episode_authorization":args.validated_c1_authorization,**frozen}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    if not args.acknowledge_model_cost or not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("Real run requires DEEPSEEK_API_KEY and --acknowledge-model-cost")
    output=args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    ledger=args.shared_ledger.resolve() if args.validated_handoff_authorization else output/"v0_1_cost_ledger.jsonl"
    if ledger_charge(ledger)+args.max_cost_usd>args.batch_cap_usd:raise RuntimeError("shared_batch_budget_blocked")
    if args.validated_c1_authorization:
        with Path(args.validated_c1_authorization["claim"]).open("x",encoding="utf-8") as stream:
            json.dump({**args.validated_c1_authorization,"claimed_at":datetime.now(timezone.utc).isoformat()},stream)
            stream.flush()
            os.fsync(stream.fileno())
    if args.validated_handoff_authorization:
        claim=Path(args.validated_handoff_authorization['claim'])
        claim.parent.mkdir(exist_ok=True)
        with claim.open('x',encoding='utf-8') as stream:
            json.dump({**args.validated_handoff_authorization,'claimed_at':datetime.now(timezone.utc).isoformat()},stream)
            stream.flush()
            os.fsync(stream.fileno())
    batch=output/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid4().hex[:6])
    save(batch/"batch_config.json",{"code":frozen,"design":args.design,"design_hash":args.design_hash,
        "organization_policy":args.design["method"],"scenario":args.scenario,"attack":args.attack,"batch_cap_usd":args.batch_cap_usd,
        "single_episode_authorization":args.validated_c1_authorization})
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
