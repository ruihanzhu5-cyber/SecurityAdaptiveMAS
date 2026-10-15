"""Prepare/freeze or run the four prescribed units through the same API and ledger."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime,timezone
import hashlib
import io
import json
import re
from pathlib import Path
import traceback
import yaml

from security_adaptive_mas.api import run
from security_adaptive_mas.config import ROOT
from security_adaptive_mas.run_framework import code_state,save,ledger_charge
from security_adaptive_mas.orchestration.configuration import load_design,design_hash

BASE=ROOT/"results/runs/v0_6_modular_pilot/20261015_01"
ORDER=("B1","B0","A0","A1")


def command(unit,dry=False):
    return ["--design-config",unit["configuration_path"],"--mode","adaptive",
        "--scenario","scenario_universe_22_kb5ah9","--attack",unit["attack"],
        "--attack-spec",str(ROOT/"configs/attack_single_injection_22_kb5ah9.json"),
        "--task-scope-policy","sandbox_permissive","--max-turns","20","--max-requests","100",
        "--max-total-tokens","800000","--max-cost-usd","0.4","--batch-cap-usd","1.6",
        "--output-dir",str(BASE),"--dry-run" if dry else "--acknowledge-model-cost"]


def study_directory(study_id=None):
    if study_id is None:return BASE
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*",study_id):
        raise ValueError("study_id must be a single name using letters, digits, underscores or hyphens")
    return BASE/"studies"/study_id


def prepare(study_id=None):
    directory=study_directory(study_id)
    manifest_path=directory/"experiment_manifest.json"
    if manifest_path.exists():raise FileExistsError("Do not overwrite an existing pilot or reset its ledger")
    directory.mkdir(parents=True,exist_ok=True)
    design=load_design(ROOT/"configs/research_v0_6.yaml")
    frozen=code_state()
    for relative in frozen["source_file_hashes"]:
        snapshot=directory/"inputs/code_snapshot"/relative
        snapshot.parent.mkdir(parents=True,exist_ok=True)
        snapshot.write_bytes((ROOT/relative).read_bytes())
    (directory/"inputs/working_tree.diff").write_bytes(__import__("subprocess").check_output(
        ["git","diff","--binary","HEAD"],cwd=ROOT))
    units={}
    for name in ORDER:
        method="modular_risk_aware" if name in {"B1","A1"} else "modular_task_only"
        value={**design,"method":method}
        path=directory/"inputs"/(method+".yaml")
        path.parent.mkdir(exist_ok=True)
        path.write_text(yaml.safe_dump(value,sort_keys=False),encoding="utf-8")
        unit={"status":"NOT_RUN","method":method,"attack":"single_injection" if name.startswith("A") else "none",
              "configuration_path":str(path),"configuration_sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
        capture=io.StringIO()
        with redirect_stdout(capture):preview=run(command(unit,dry=True))
        (directory/"inputs"/(name+"_dry_run.json")).write_text(capture.getvalue(),encoding="utf-8")
        unit["design_hash"]=preview["design_hash"]
        unit["argv"]=command(unit)
        units[name]=unit
    save(manifest_path,{"study_id":study_id or BASE.name,
        "study_directory":str(directory),"shared_ledger":str(BASE/"v0_1_cost_ledger.jsonl"),
        "ledger_charge_usd_before_study":ledger_charge(BASE/"v0_1_cost_ledger.jsonl"),"client_date":"2026-10-15","prepared_at_utc":datetime.now(timezone.utc).isoformat(),
        "code":frozen,"order":ORDER,"budget":{"per_unit_usd":.4,"total_usd":1.6,"max_requests":100,"max_total_tokens":800000,"max_turns":20},
        "units":units,"status":"PREPARED_NO_PAID_REQUESTS","raw_logs":"local_ignored_not_for_GitHub",
        "local_paths_sha256":hashlib.sha256((ROOT/"work/v0_6_local_paths.yaml").read_bytes()).hexdigest()})
    print("Four dry-runs prepared; zero paid requests;",manifest_path)


def execute(study_id=None):
    manifest_path=study_directory(study_id)/"experiment_manifest.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["status"]!="PREPARED_NO_PAID_REQUESTS":raise RuntimeError("This pilot cannot be repeated automatically")
    if any(u["status"]!="NOT_RUN" for u in manifest["units"].values()):raise RuntimeError("unit_already_attempted")
    for name in ORDER:
        if code_state()!=manifest["code"]:raise RuntimeError("frozen_working_tree_changed")
        if hashlib.sha256((ROOT/"work/v0_6_local_paths.yaml").read_bytes()).hexdigest()!=manifest["local_paths_sha256"]:
            raise RuntimeError("local_paths_changed")
        unit=manifest["units"][name]
        path=Path(unit["configuration_path"])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=unit["configuration_sha256"] or design_hash(load_design(path))!=unit["design_hash"]:
            raise RuntimeError("frozen_configuration_changed")
        unit.update(status="RUNNING",started_at_utc=datetime.now(timezone.utc).isoformat())
        save(manifest_path,manifest)
        try:
            outcome=run(command(unit))
            summary=outcome["summaries"][0]
            unit.update(status="FAILED" if summary["exception_type"] else "FINISHED",run_id=summary["run_id"],
                run_dir=str(Path(outcome["batch_dir"])/summary["scenario_id"]/summary["run_id"]),
                results={k:summary[k] for k in ("exception_type","stop_reason","official_runner_success","legitimate_task_completed",
                    "model_requests_submitted","successful_model_responses","input_tokens_total","output_tokens_total",
                    "cost_estimate_usd","unknown_cost_bound","wall_time_seconds","effective_graph_patches")})
            blocked=summary["exception_type"] is not None and summary["exception_type"] not in {"BudgetExceeded","MaxIterationsAgentError"}
        except Exception as error:
            unit.update(status="BLOCKED",error_type=type(error).__name__,reason=str(error),traceback=traceback.format_exc())
            blocked=True
        unit["finished_at_utc"]=datetime.now(timezone.utc).isoformat()
        manifest["ledger_charge_usd_including_unknown"]=ledger_charge(BASE/"v0_1_cost_ledger.jsonl")
        save(manifest_path,manifest)
        print(json.dumps({"experiment_id":name,"status":unit["status"],"run_id":unit.get("run_id"),"results":unit.get("results")},ensure_ascii=False),flush=True)
        if blocked:
            manifest["status"]="BLOCKED_STOPPED_AFTER_"+name
            for later in ORDER:
                if manifest["units"][later]["status"]=="NOT_RUN":manifest["units"][later]["blocked_by"]=name+":integration_stop_gate"
            save(manifest_path,manifest)
            return
    manifest["status"]="FOUR_UNITS_ATTEMPTED"
    save(manifest_path,manifest)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("prepare","run"))
    parser.add_argument("--study-id",help="New manifest/snapshot name; all studies retain the original shared cost ledger")
    parser.add_argument("--acknowledge-model-cost",action="store_true")
    args=parser.parse_args()
    if args.action=="prepare":prepare(args.study_id)
    else:
        if not args.acknowledge_model_cost:parser.error("Paid pilot needs explicit cost acknowledgment")
        execute(args.study_id)
