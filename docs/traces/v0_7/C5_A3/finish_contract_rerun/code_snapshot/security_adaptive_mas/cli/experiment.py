"""Frozen plans and explicitly bounded execution through run_framework."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .. import api
from ..config import ROOT
from ..experiment_suite import METHODS, make_matrix
from ..organization_contracts import load_design_value


def create_plan(config_path, output, split):
    from ..run_framework import code_state
    design = api.load_configuration(config_path)
    manifest_path = ROOT / design["data_manifest"]
    manifest = api.read_result(manifest_path)
    selected = {**manifest, "records": [r for r in manifest["records"] if r["split"] == split]}
    result = make_matrix(selected, design, [design["seed"]])
    result.update(design_config=str(Path(config_path).resolve()),
                  design_file_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
                  manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                  code=code_state(), split=split, execution_status="NOT_RUN")
    api.write_new_json(output, result)
    return result


def execute_plan(args):
    from ..run_framework import code_state, ledger_charge
    if not args.acknowledge_model_cost:
        raise ValueError("Real experiments require --acknowledge-model-cost")
    if args.limit < 1 or not 0 < args.max_total_cost_usd <= 5 or not 0 < args.max_cost_usd <= 1:
        raise ValueError("Explicit positive limit and cost caps required (total <=5, episode <=1 USD)")
    if args.max_cost_usd > args.max_total_cost_usd:
        raise ValueError("Per-episode reservation exceeds total cost cap")
    plan = api.read_result(args.plan)
    config_path = Path(plan["design_config"])
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != plan["design_file_sha256"]:
        raise ValueError("plan_design_changed")
    design = api.load_configuration(config_path)
    if hashlib.sha256((ROOT / design["data_manifest"]).read_bytes()).hexdigest() != plan["manifest_sha256"]:
        raise ValueError("plan_manifest_changed")
    if code_state() != plan["code"]:
        raise ValueError("plan_code_snapshot_changed_regenerate_plan")
    chosen = plan["runs"][:args.limit]
    # Validate all selected units before any real request, including the matrix identity.
    manifest = api.read_result(ROOT / design["data_manifest"])
    records = {r["scenario_id"]: r for r in manifest["records"]}
    for row in chosen:
        if row["method"] not in METHODS or row["attack"] != "none" or row["model"] != "deepseek-flash":
            raise ValueError("UNSUPPORTED_matrix_method_attack_or_model")
        if row["status"] != "NOT_RUN" or row["design"] != design:
            raise ValueError("plan_unit_status_or_configuration_changed")
        if row["scenario"] != records[row["scenario"]["scenario_id"]] or row["scenario"]["benchmark"] != "gaia2":
            raise ValueError("UNSUPPORTED_matrix_dataset_identity")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record_path = args.output_dir / ("execution_" + uuid4().hex[:12] + ".json")
    record = {"plan": str(args.plan.resolve()), "limit": args.limit, "units": [],
              "max_total_cost_usd": args.max_total_cost_usd, "status": "RUNNING",
              "started_at": datetime.now(timezone.utc).isoformat()}
    api.write_new_json(record_path, record)
    for row in chosen:
        ledger = args.output_dir / "v0_1_cost_ledger.jsonl"
        if ledger_charge(ledger) + args.max_cost_usd > args.max_total_cost_usd:
            record["units"].append({"scenario_id": row["scenario"]["scenario_id"],
                                    "method": row["method"], "status": "BLOCKED_TOTAL_BUDGET"})
            break
        effective = deepcopy(design)
        effective.update(method=row["method"], seed=row["seed"])
        load_design_value(effective)
        unit = {"scenario_id": row["scenario"]["scenario_id"], "method": row["method"], "status": "STARTED"}
        record["units"].append(unit)
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        argv = ["--design-config", str(config_path), "--mode", "single" if row["method"] == "single" else "adaptive",
                "--scenario", row["scenario"]["scenario_id"], "--dataset-category", row["scenario"]["category"],
                "--max-cost-usd", str(args.max_cost_usd), "--batch-cap-usd", str(args.max_total_cost_usd),
                "--max-turns", str(args.max_turns), "--max-requests", str(args.max_requests),
                "--max-total-tokens", str(args.max_total_tokens), "--output-dir", str(args.output_dir),
                "--acknowledge-model-cost"]
        try:
            result = api.run(argv, design=effective)
        except (Exception, SystemExit) as error:
            unit.update(status="FAILED", exception_type=type(error).__name__, reason=str(error),
                        evidence="runner summaries and ledger retained if execution started")
            record["status"] = "FAILED"
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        unit.update(status="FINISHED", batch_dir=result["batch_dir"])
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    record["status"] = "STOPPED_BUDGET" if any(u["status"] == "BLOCKED_TOTAL_BUDGET" for u in record["units"]) else "FINISHED"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--design-config", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--split", choices=("development", "frozen_evaluation"), default="development")
    run = sub.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--limit", type=int, required=True)
    run.add_argument("--max-total-cost-usd", type=float, required=True)
    run.add_argument("--acknowledge-model-cost", action="store_true")
    run.add_argument("--max-cost-usd", type=float, default=1)
    run.add_argument("--max-turns", type=int, default=20)
    run.add_argument("--max-requests", type=int, default=100)
    run.add_argument("--max-total-tokens", type=int, default=800000)
    run.add_argument("--output-dir", type=Path, default=ROOT / "results/v0_5_matrix")
    args = parser.parse_args(argv)
    if args.action == "plan":
        result = create_plan(args.design_config, args.output, args.split)
        print("NOT_RUN units", len(result["runs"]), "->", args.output)
        return result
    return execute_plan(args)
