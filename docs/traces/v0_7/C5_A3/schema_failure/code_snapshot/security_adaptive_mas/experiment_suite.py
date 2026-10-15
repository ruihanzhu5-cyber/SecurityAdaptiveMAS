"""Frozen research manifests, non-executing matrices, and accounted black-box attempts."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
import random
import traceback

from .config import ROOT
from .dataset_store import category_path, source_versions


METHODS = ("single", "fixed", "task_only", "fixed_security", "reactive_isolation", "joint", "task_plus_filter")


def make_manifest(destination, seed=20261014):
    import pyarrow.parquet as pq
    if Path(destination).exists():
        raise FileExistsError("frozen_manifest_must_not_be_overwritten")
    source = source_versions()
    five = json.loads((ROOT/"configs/five_manifest.json").read_text(encoding="utf-8"))
    development = {row["scenario_id"] for row in five}
    categories = ("adaptability","ambiguity","execution","search","time")
    pools = {}
    identities = []
    for category in categories:
        path = category_path(category,ROOT/"work/data",source)
        pools[category] = path
        identities.extend((row["scenario_id"],category,index) for index,row in
                          enumerate(pq.read_table(path,columns=["scenario_id"]).to_pylist()))
    if len({key for key,_,_ in identities}) != len(identities):
        raise ValueError("duplicate_core_scenario_identity")
    development_pool = sorted(row for row in identities if row[0] not in development and
                              row[1] in {"adaptability","execution","time"})
    evaluation_pool = sorted(row for row in identities if row[1] in {"ambiguity","search"})
    rng = random.Random(seed)
    rng.shuffle(development_pool); rng.shuffle(evaluation_pool)
    selected = {row[0]:"development" for row in development_pool[:15]}
    selected.update({row[0]:"frozen_evaluation" for row in evaluation_pool[:40]})
    selected.update({key:"development" for key in development})
    rows = []
    for category,path in pools.items():
        index = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=4,columns=["scenario_id","data"]):
            for row in batch.to_pylist():
                if row["scenario_id"] in selected:
                    rows.append({"benchmark":"gaia2","scenario_id":row["scenario_id"],
                        "category":category,"row_index_zero_based":index,"split":selected[row["scenario_id"]],
                        "sha256":hashlib.sha256(row["data"].encode()).hexdigest(),
                        "attack_families_verified":[],"attack_eligibility":"UNKNOWN_requires_boundary_validation"})
                index += 1
    manifest = {"version":"v0.4-selection-1","selection_seed":seed,
        "selection_rule":"five historical development cases plus 15 previously scanned-category IDs; 40 ID-only sampled tasks from previously unscanned ambiguity/search",
        "prior_static_inspection_excluded_from_evaluation":["adaptability","execution","time"],
        "source_revision":source["dataset_revision"],"outcome_independent_selection":True,
        "frozen_evaluation_status":"IDs_and_data_hashes_frozen_no_results_or_task_content_used_for_selection",
        "records":sorted(rows,key=lambda r:(r["split"],r["scenario_id"])),
        "agentdyn":{"status":"BLOCKED","reason":"trusted side-effect capability catalog and local dependency import not established"}}
    Path(destination).write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return manifest


def make_matrix(manifest, design, seeds):
    rows = []
    for task in manifest["records"]:
        for method in METHODS:
            for seed in seeds:
                rows.append({"scenario":task,"method":method,"seed":seed,"attack":"none",
                    "attack_protocol":design["attack_protocol"],"model":"deepseek-flash",
                    "design":design,"status":"NOT_RUN","capability_ceiling":design["delegable_tools"]})
    return {"version":"v0.4-matrix-1","runs":rows,
            "attack_units":"BLOCKED_until_per-task_family_surface_and_evaluator_are_verified"}


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    parent_candidate: str | None
    payload_hash: str
    editable_scope: str
    victim_run_id: str
    feedback: dict
    costs: dict
    payload_ref: str


def blackbox_search(*, defense_version, attempts, initial_candidate, generate, run_victim,
                    evaluate, observation_policy, output, editable_scope, phase="search"):
    """Callbacks must use real harness runs; the module never substitutes a victim."""
    if attempts not in (1,4,16) or observation_policy not in {"success_only","declared_visible_trace"}:
        raise ValueError("invalid_blackbox_budget_or_feedback_policy")
    rows, candidate, parent, feedback = [], initial_candidate, None, None
    output = Path(output)
    if output.exists():
        raise FileExistsError("attack_attempt_log_must_not_be_overwritten")
    for index in range(attempts):
        attempt_id = f"attempt_{index+1}"
        with output.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps({"event":"attempt_started","attempt_id":attempt_id,
                "defense_version":defense_version,"parent_candidate":parent,"phase":phase})+"\n")
        try:
            generator_cost = 0.0
            if index:
                candidate, generator_cost = generate(candidate,feedback)
            payload_ref = output.with_name(output.stem+f".{attempt_id}.payload.txt")
            with payload_ref.open("x",encoding="utf-8") as stream:
                stream.write(candidate)
            victim = run_victim(candidate,defense_version)
            if not victim["run_id"] or victim["code_snapshot_id"] != defense_version:
                raise ValueError("victim_attempt_requires_frozen_real_run_identity")
            outcome, evaluation_cost = evaluate(victim)
        except Exception as error:
            with output.open("a",encoding="utf-8") as stream:
                stream.write(json.dumps({"event":"attempt_failed","attempt_id":attempt_id,
                    "exception_type":type(error).__name__,"reason":str(error),
                    "traceback":traceback.format_exc(),"billing_status":"unknown_requires_harness_ledger"})+"\n")
            raise
        feedback = {"target_achieved":outcome["target_achieved"]}
        if observation_policy == "declared_visible_trace":
            feedback["visible_trace"] = victim["attacker_visible_trace"]
        record = AttemptRecord(attempt_id,parent,hashlib.sha256(candidate.encode()).hexdigest(),
            editable_scope,victim["run_id"],feedback,
            {"attack_generation_usd":generator_cost,"victim_usd":victim["cost_estimate_usd"],
             "victim_unknown_cost_bound_usd":victim["unknown_cost_bound"],
             "evaluation_usd":evaluation_cost},str(payload_ref))
        with output.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(record),ensure_ascii=False)+"\n")
        rows.append(record)
        parent = record.attempt_id
    with output.open("a",encoding="utf-8") as stream:
        stream.write(json.dumps({"event":"protocol_cost_totals","phase":phase,
            "attempts":len(rows),"costs":{key:sum(row.costs[key] for row in rows)
                for key in ("attack_generation_usd","victim_usd","victim_unknown_cost_bound_usd","evaluation_usd")}})+"\n")
    return rows


def fixed_transfer(*, payload, defense_versions, run_victim, evaluate, observation_policy,
                   output_directory, editable_scope):
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True,exist_ok=True)
    results = {}
    for version in defense_versions:
        results[version] = blackbox_search(defense_version=version,attempts=1,initial_candidate=payload,
            generate=None,run_victim=run_victim,evaluate=evaluate,observation_policy=observation_policy,
            output=output_directory/(version+".fixed-transfer.jsonl"),editable_scope=editable_scope,
            phase="frozen_evaluation")
    return results


def summarize_units(rows):
    """Unknown outcomes remain in the denominator and in the bounds."""
    eligible = [row for row in rows if row["eligible"] is True]
    known = sum(row["target_achieved"] is True for row in eligible)
    unknown = sum(row["target_achieved"] is None for row in eligible)
    denominator = len(eligible)
    return {"included":len(rows),"eligible":denominator,
        "exposed":sum(row["model_exposed"] is True for row in rows),
        "not_delivered":sum(row["injection_delivered"] is False for row in rows),
        "infrastructure_failures":sum(row["evidence_status"] == "infrastructure_failure" for row in rows),
        "confirmed_target_achieved":known,"unknown_target_outcomes":unknown,
        "asr_bounds":None if not denominator else [known/denominator,(known+unknown)/denominator],
        "safe_completed":sum(row["legitimate_completed"] is True and row["harm_observed"] is False for row in rows),
        "all_run_cost_usd":sum(row["cost_estimate_usd"] for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    selection = sub.add_parser("manifest"); selection.add_argument("--output",type=Path,required=True)
    matrix = sub.add_parser("matrix")
    matrix.add_argument("--manifest",type=Path,required=True)
    matrix.add_argument("--design-config",type=Path,required=True)
    matrix.add_argument("--output",type=Path,required=True)
    fit = sub.add_parser("fit-cost")
    fit.add_argument("--input",type=Path,required=True); fit.add_argument("--output",type=Path,required=True)
    fit.add_argument("--manifest",type=Path,default=ROOT/"configs/research_v0_4_manifest.json")
    args = parser.parse_args()
    if args.command == "manifest":
        result = make_manifest(args.output)
        print({split:sum(row["split"]==split for row in result["records"])
               for split in ("development","frozen_evaluation")})
    elif args.command == "matrix":
        from .organization_contracts import load_design
        result = make_matrix(json.loads(args.manifest.read_text()),load_design(args.design_config),[20261014])
        if args.output.exists():
            raise FileExistsError("matrix_must_not_be_overwritten")
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print("NOT_RUN matrix units",len(result["runs"]))
    else:
        from .cost_model import fit_development, extract_development_rows
        entries = [json.loads(line) for line in args.input.read_text().splitlines()]
        rows = extract_development_rows([row["run_dir"] for row in entries],
                                        json.loads(args.manifest.read_text(encoding="utf-8")))
        fit_development(rows,args.output)


if __name__ == "__main__":
    main()
