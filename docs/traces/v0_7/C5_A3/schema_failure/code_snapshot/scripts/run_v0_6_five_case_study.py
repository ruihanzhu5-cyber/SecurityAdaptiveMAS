"""Sequential 20-unit study using the existing API, official Runner and historical ledger."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import traceback
import yaml

from security_adaptive_mas.config import ROOT
from security_adaptive_mas.api import run
from security_adaptive_mas.run_framework import code_state,ledger_charge,save
from security_adaptive_mas.orchestration.configuration import load_design
from scripts.preflight_v0_6_five_cases import preflight

ORDER=("B1","B0","A0","A1")


def units_for(matrix):
    units=[]
    for case in matrix:
        for condition in ORDER:
            attack=condition.startswith("A")
            ready=case["attack_ready"] if attack else case["benign_ready"]
            units.append({"unit_id":case["case_id"]+"_"+condition,"case_id":case["case_id"],"condition":condition,
                "scenario_id":case["scenario_id"],"method":"modular_risk_aware" if condition in {"B1","A1"} else "modular_task_only",
                "attack":case.get("attack_mode","single_entry_tool_result_text") if attack else "none","status":"READY" if ready else case["status"],
                "legitimate_scope":case["legitimate_scope"],"readiness_reason":case["reason"],
                "attack_config":case.get("attack_config") if attack else None,
                "attack_config_sha256":case.get("attack_config_sha256") if attack else None})
    if len(units)!=20:raise ValueError("exactly_twenty_units_required")
    return units


def command(unit,manifest,path,*,dry=False):
    blocked=unit["status"].startswith("BLOCKED")
    attack="none" if dry and blocked else unit["attack"]
    argv=["--design-config",unit["configuration_path"],"--mode","adaptive","--scenario",unit["scenario_id"],
        "--attack",attack,"--task-scope-policy","sandbox_permissive","--max-turns","20","--max-requests","100",
        "--max-total-tokens","800000","--max-cost-usd","0.4","--batch-cap-usd",str(1.6 if dry else manifest["absolute_cap_usd"]),
        "--output-dir",str(Path(manifest["shared_ledger"]).parent)]
    if attack!="none":argv.extend(["--attack-spec",unit["attack_config"]])
    if dry:argv.append("--dry-run")
    else:argv.extend(["--study-manifest",str(path),"--study-unit",unit["unit_id"],"--acknowledge-model-cost"])
    return argv


def prepare(directory,ledger,attack_configs=None):
    directory=Path(directory).resolve();ledger=Path(ledger).resolve();path=directory/"study_manifest.json"
    if not directory.is_relative_to(ROOT/"results"):raise ValueError("study_directory_must_be_local_results")
    if path.exists():raise FileExistsError("study_manifest_already_exists")
    if not ledger.is_file():raise FileNotFoundError("historical_ledger_required")
    frozen=code_state()
    if frozen["dirty_status"]:raise ValueError("commit_changes_before_prepare")
    matrix=preflight(directory/"preflight",attack_configs);directory.mkdir(parents=True,exist_ok=True)
    inputs=directory/"inputs";inputs.mkdir(exist_ok=True)
    before=ledger_charge(ledger)
    manifest={"kind":"v0_6_five_case_20","study_id":directory.name,"status":"PREPARED","prepared_at_utc":datetime.now(timezone.utc).isoformat(),
        "code":frozen,"shared_ledger":str(ledger),"ledger_charge_before":before,"study_additional_cap_usd":8.0,
        "absolute_cap_usd":before+8.0,"per_episode_cap_usd":0.4,"units":units_for(matrix),"frozen_input_hashes":{},
        "upstreams":json.loads((ROOT/"configs/upstream_v0_6.lock.json").read_text(encoding="utf-8")),
        "fee_authorization":"User explicitly authorized this study additional <=8 USD and each episode <=0.40 USD"}
    shutil.copyfile(ledger,inputs/"ledger_before.jsonl")
    design=load_design(ROOT/"configs/research_v0_6.yaml")
    for method in ("modular_risk_aware","modular_task_only"):
        config=inputs/(method+".yaml");config.write_text(yaml.safe_dump({**design,"method":method},sort_keys=False),encoding="utf-8")
    for unit in manifest["units"]:
        unit["configuration_path"]=str(inputs/(unit["method"]+".yaml"))
        capture=io.StringIO()
        with redirect_stdout(capture):preview=run(command(unit,manifest,path,dry=True))
        (inputs/(unit["unit_id"]+"_dry_run.json")).write_text(capture.getvalue(),encoding="utf-8")
        unit["design_hash"]=preview["design_hash"]
        unit["dry_validation_scope"]="base_case_only_attack_blocked" if unit["status"].startswith("BLOCKED") else "complete_declared_unit"
        unit["argv"]=None if unit["status"].startswith("BLOCKED") else command(unit,manifest,path)
    for rel in frozen["source_file_hashes"]:
        target=inputs/"code_snapshot"/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/rel).read_bytes())
    test_hashes={}
    for source in (ROOT/"tests").glob("*.py"):
        target=inputs/"test_snapshot"/source.name;target.parent.mkdir(exist_ok=True);target.write_bytes(source.read_bytes())
        test_hashes[source.name]=hashlib.sha256(source.read_bytes()).hexdigest()
    manifest["test_hashes"]=test_hashes
    for source in [*inputs.glob("*.yaml"),*directory.glob("preflight/C*/preflight.json"),ROOT/"work/v0_6_local_paths.yaml"]:
        manifest["frozen_input_hashes"][str(source)]=hashlib.sha256(source.read_bytes()).hexdigest()
    save(path,manifest)
    print(json.dumps({"study_manifest":str(path),"units":len(manifest["units"]),"ready":sum(u["status"]=="READY" for u in manifest["units"]),"ledger_before":before,"absolute_cap":before+8.0}))
    return manifest


def execute(directory):
    path=Path(directory).resolve()/"study_manifest.json";manifest=json.loads(path.read_text(encoding="utf-8"))
    if manifest["status"]!="PREPARED":raise RuntimeError("study_already_attempted")
    manifest["status"]="RUNNING";save(path,manifest)
    for unit in manifest["units"]:
        if unit["status"]!="READY":continue
        if code_state()!=manifest["code"]:raise RuntimeError("frozen_study_code_changed")
        if ledger_charge(Path(manifest["shared_ledger"]))+0.4>manifest["absolute_cap_usd"]:
            unit.update(status="BLOCKED_BUDGET",reason="shared_ledger_plus_episode_reservation_exceeds_absolute_cap");save(path,manifest);continue
        unit.update(status="RUNNING",started_at_utc=datetime.now(timezone.utc).isoformat());save(path,manifest)
        fatal=False
        try:
            outcome=run(command(unit,manifest,path));summary=outcome["summaries"][0]
            runtime_status=summary.get("run_status")
            unit.update(status=runtime_status if runtime_status in {"INDETERMINATE_IDLE","BLOCKED_EVALUATION"} else
                "FAILED" if summary["exception_type"] else "FINISHED",run_id=summary["run_id"],
                run_dir=str(Path(outcome["batch_dir"])/summary["scenario_id"]/summary["run_id"]),
                results={k:summary[k] for k in ["exception_type","stop_reason","official_runner_success","legitimate_task_completed","model_requests_submitted","successful_model_responses","input_tokens_total","output_tokens_total","cost_estimate_usd","unknown_cost_bound","wall_time_seconds","effective_graph_patches"]})
            fatal=summary["exception_type"] not in {None,"BudgetExceeded","MaxIterationsAgentError","UnavailableToolAgentError"}
            fatal=fatal or runtime_status in {"INDETERMINATE_IDLE","BLOCKED_EVALUATION"}
        except (Exception,SystemExit) as error:
            unit.update(status="FAILED",error_type=type(error).__name__,reason=str(error),traceback=traceback.format_exc());fatal=True
        unit["finished_at_utc"]=datetime.now(timezone.utc).isoformat()
        manifest["ledger_charge_after"]=ledger_charge(Path(manifest["shared_ledger"]))
        save(path,manifest);print(json.dumps({"unit":unit["unit_id"],"status":unit["status"],"results":unit.get("results"),"reason":unit.get("reason")},ensure_ascii=False),flush=True)
        if fatal:
            manifest["status"]="STOPPED_SYSTEMIC_FAILURE"
            for remaining in manifest["units"]:
                if remaining["status"]=="READY":remaining.update(status="NOT_RUN",blocked_by=unit["unit_id"])
            save(path,manifest);return manifest
    manifest["status"]="ALL_ELIGIBLE_UNITS_ATTEMPTED";save(path,manifest)
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("action",choices=("prepare","run"))
    parser.add_argument("--attack-config",type=Path,action="append")
    parser.add_argument("--study-dir",type=Path,required=True);parser.add_argument("--ledger",type=Path)
    parser.add_argument("--acknowledge-model-cost",action="store_true");args=parser.parse_args()
    if args.action=="prepare":
        if args.ledger is None:parser.error("prepare requires the existing --ledger")
        prepare(args.study_dir,args.ledger,args.attack_config)
    else:
        if not args.acknowledge_model_cost:parser.error("paid study requires explicit cost acknowledgment")
        execute(args.study_dir)
