"""Read existing evidence without changing original results or inferring success."""
import argparse
import hashlib
import json
from pathlib import Path

from ..api import read_result, write_new_json


def known(value):
    # Historical mailbox scorer used the literal strings "true" / "false".
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    return "unknown"


def summarize(run_path):
    path = Path(run_path)
    source = path / "summary.json" if path.is_dir() else path
    row = read_result(source)
    scoring = row.get("attack_scoring") or {}
    files = {name: (source.parent / name).is_file() for name in (
        "run_config.json", "framework_trace.jsonl", "topology_events.jsonl", "summary.json", "evaluation.json", "runner.log")}
    trace_path = source.parent / "framework_trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()] if files["framework_trace.jsonl"] else None
    patches = ([event for event in events if event["kind"] == "topology_patch_applied"]
               if events is not None else None)
    # Summary counts for legacy runs; newer submitted events exclude budget prechecks.
    submitted = [e for e in events if e["kind"] == "model_request_submitted"] if events is not None else []
    return {
        "source": str(source.resolve()), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "run_id": row.get("run_id", "unknown"), "scenario_id": row.get("scenario_id", "unknown"),
        "code_revision": row.get("code_revision", "unknown"), "design_hash": row.get("design_hash", "unknown"),
        "code_snapshot_id": row.get("code_snapshot_id", "unknown"),
        "official_runner_success": known(row.get("official_runner_success")),
        "stop_reason": row.get("stop_reason", "unknown"), "exception_type": row.get("exception_type"),
        "validation_status": row.get("validation_status", "unknown_legacy_field_missing"),
        "legitimate_task_completed": known(row.get("legitimate_task_completed")),
        "semantic_verified": known(row.get("semantic_verified")),
        "attack_delivered": known(scoring.get("attack_delivered", row.get("attack_delivered"))),
        "model_exposed_instances": scoring.get("exposed_instances", "unknown"),
        "target_attempted": known(scoring.get("target_attempted", row.get("target_attempted"))),
        "attack_target_achieved": known(scoring.get("target_achieved", row.get("target_achieved"))),
        "gateway_blocked": known(scoring.get("blocked_by_gateway", row.get("gateway_blocked"))),
        "model_requests_total": row.get("model_requests_total", "unknown"),
        "model_requests_submitted": row.get("model_requests_submitted",
            len(submitted) if submitted else "unknown_legacy_or_no_submissions"),
        "input_tokens_total": row.get("input_tokens_total", "unknown"),
        "output_tokens_total": row.get("output_tokens_total", "unknown"),
        "cost_estimate_usd": row.get("cost_estimate_usd", "unknown"),
        "unknown_cost_bound": row.get("unknown_cost_bound", "unknown"),
        "cost_scope": "historical_rate_estimate_not_provider_bill",
        "wall_time_seconds": row.get("wall_time_seconds", "unknown"),
        "candidate_decisions": sum(e["kind"] == "joint_decision" for e in events) if events is not None else "unknown",
        "effective_graph_patches": sum(e["graph_version"] != e["previous_version"] for e in patches) if patches is not None else row.get("effective_graph_patches", "unknown"),
        "effective_reconfigurations": row.get("effective_reconfigurations", "unknown"),
        "files_present": files, "raw_attack_scoring": scoring,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.runs.is_file():
        paths = [args.runs]
    elif (args.runs / "summary.json").is_file():
        paths = [args.runs / "summary.json"]
    else:
        paths = sorted(args.runs.rglob("summary.json"))
    if not paths:
        raise FileNotFoundError("No real summary.json found; no results fabricated")
    report = {"source_count": len(paths), "runs": [summarize(p) for p in paths],
              "missing_field_policy": "unknown_not_success", "model_calls": 0}
    write_new_json(args.output, report)
    print("Read-only report", len(paths), "runs ->", args.output)
    return report
