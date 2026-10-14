"""Declared work proxies and development-only empirical calibration."""
from __future__ import annotations

from dataclasses import asdict
import json
import hashlib
from pathlib import Path


FEATURES = ("procedure_steps", "tool_count", "protocol_rounds", "context_characters",
            "repair_count", "model_steps")
TARGETS = ("input_tokens", "output_tokens", "requests", "retries", "model_latency_seconds")


def render_objects(object_ids, provenance, compact):
    packets, seen = [], {}
    for key in object_ids:
        text = provenance.items[key].text
        if compact and text in seen:
            packets.append({"object_id":key,"same_text_as":seen[text],"source_identity_retained":True})
        else:
            packets.append({"object_id":key,"text":text})
            seen[text] = key
    return packets


def plan_features(plan, runtime, design):
    roles = [role for _,role in plan.role_specs]
    consumers = {consumer for consumer,key in plan.existing_object_deliveries}
    rendered = sum(len(json.dumps(render_objects([key for owner,key in plan.existing_object_deliveries if owner == consumer],
        runtime.controller.provenance,design["ablations"]["compact_inputs"]),ensure_ascii=False)) for consumer in consumers)
    return [sum(len(r.procedure) for r in roles), sum(len(r.requested_tools) for r in roles),
            plan.protocol_spec["rounds"],
            rendered, len(plan.pending_work),
            sum(r.step_budget for r in roles)*plan.protocol_spec["rounds"] + 1]


def assess_cost(plan, runtime, design):
    values = plan_features(plan, runtime, design)
    decision = sum(row["cost_estimate_usd"] for purpose,row in
                   runtime.controller.run_budget.actual_usage_by_purpose.items()
                   if purpose in {"task_analysis", "risk_assessment", "organization_proposal"}) if runtime.controller.run_budget else None
    result = {"mode": design["cost_mode"], "features": dict(zip(FEATURES, values)),
        "decision_cost_already_in_episode_usd": decision,
        "context_migration_characters": values[3], "pending_work_count": values[4],
        "remaining_declared_model_steps_upper_bound": values[5],
        "declared_input_characters": values[3] + sum(len(json.dumps(asdict(r))) for _,r in plan.role_specs),
        "predicted_output_tokens": None, "predicted_remaining_tokens": None,
        "status": "declared_work_proxy", "unknown": ["tool_return_size", "output_tokens", "remaining_goal_expansion"],
        "boundary": sorted({r.task_id for _,r in plan.role_specs}),
        "unit": "declared_steps_and_characters_not_tokens"}
    if design["cost_mode"] == "empirical":
        model = json.loads(Path(design["calibration_file"]).read_text(encoding="utf-8"))
        if (model["version"] != "v0.4-cost-2" or model["features"] != list(FEATURES)
                or model["targets"] != list(TARGETS) or model["split"] != "development"):
            raise ValueError("empirical_calibration_version_or_split_mismatch")
        if any(not low <= value <= high for value,(low,high) in zip(values,model["feature_ranges"])):
            raise ValueError("UNDECIDABLE_empirical_feature_outside_calibration")
        predictions = {target:model["intercepts"][i]+sum(c*v for c,v in zip(model["coefficients"][i],values))
                       for i,target in enumerate(TARGETS)}
        if any(value < 0 for value in predictions.values()) or predictions["requests"] <= 0:
            raise ValueError("UNDECIDABLE_empirical_invalid_remaining_work")
        intervals = {key:[max(0,value-model["mae"][i]),value+model["mae"][i]]
                     for i,(key,value) in enumerate(predictions.items())}
        result.update(status="empirical_estimate",
            predicted_remaining_tokens=predictions["input_tokens"]+predictions["output_tokens"],
            empirical_predictions=predictions,empirical_intervals=intervals,
            predicted_cost_estimate_usd=predictions["input_tokens"]*design["rates"]["input_usd_per_token"]+
                                        predictions["output_tokens"]*design["rates"]["output_usd_per_token"],
            calibration_run_ids=model["run_ids"], calibration_version=model["version"],
            unit="estimated_tokens_not_actual_usage")
    return result


def fit_development(rows, destination):
    """One row per real development prefix/continuation, never report totals as samples."""
    import numpy as np
    if Path(destination).exists():
        raise FileExistsError("calibration_must_be_saved_as_a_new_version")
    if len(rows) < len(FEATURES)+2 or any(row["split"] != "development" or
            not row["run_id"] or row["evidence_status"] != "actual_usage" for row in rows):
        raise ValueError("BLOCKED_insufficient_real_development_continuations")
    x = np.array([[row["features"][key] for key in FEATURES] for row in rows], dtype=float)
    y = np.array([[row["actual_remaining"][key] for key in TARGETS] for row in rows],dtype=float)
    augmented = np.column_stack((np.ones(len(rows)),x))
    coefficients, _, rank, _ = np.linalg.lstsq(augmented,y,rcond=None)
    if rank < len(FEATURES)+1:
        raise ValueError("BLOCKED_calibration_features_not_identifiable")
    result = {"version":"v0.4-cost-2","split":"development","features":list(FEATURES),"targets":list(TARGETS),
              "run_ids":[row["run_id"] for row in rows],"intercepts":coefficients[0].tolist(),
              "coefficients":coefficients[1:].T.tolist(),
              "training_trace_hashes":sorted({row["source_trace_sha256"] for row in rows}),
              "feature_ranges":[[float(x[:,i].min()),float(x[:,i].max())] for i in range(x.shape[1])],
              "mae":np.abs(augmented@coefficients-y).mean(axis=0).tolist(),"error_scope":"training_residual_not_heldout_error"}
    Path(destination).write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def extract_development_rows(run_directories, manifest):
    """Targets come from subsequent real usage events, never a report's totals."""
    eligible = {r["scenario_id"] for r in manifest["records"] if r["split"] == "development"}
    samples = []
    for directory in run_directories:
        directory = Path(directory)
        config = json.loads((directory/"run_config.json").read_text(encoding="utf-8"))
        if config["scenario_id"] not in eligible or config["split"] != "development" or not config.get("design"):
            raise ValueError("calibration_requires_v04_development_run")
        summary = json.loads((directory/"summary.json").read_text(encoding="utf-8"))
        if summary["legitimate_task_status"] != "completed" or not summary.get("legitimate_task_evaluation_evidence"):
            raise ValueError("BLOCKED_calibration_requires_independently_verified_task_completion")
        raw = (directory/"framework_trace.jsonl").read_bytes()
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        if any(event["mode"] == "offline_mock" for event in events):
            raise ValueError("offline_fixtures_are_not_cost_calibration_data")
        if any(event["kind"] == "model_usage_unknown" for event in events):
            raise ValueError("calibration_usage_is_incomplete")
        for index,event in enumerate(events):
            if event["kind"] != "joint_decision" or event.get("design_version") != "v0.4":
                continue
            future_usage = [row for row in events[index+1:] if row["kind"] == "model_usage"]
            if not future_usage:
                continue
            samples.append({"split":"development","run_id":config["run_id"],
                "scenario_id":config["scenario_id"],"prefix_event_index":index,
                "features":event["rewrite_plan"]["cost_assessment"]["features"],
                "actual_remaining":{"input_tokens":sum(row["prompt_tokens"] for row in future_usage),
                    "output_tokens":sum(row["completion_tokens"] for row in future_usage),
                    "requests":sum(row["kind"] == "model_request_submitted" for row in events[index+1:]),
                    "retries":sum(row["kind"] == "model_retryable_error" for row in events[index+1:]),
                    "model_latency_seconds":sum(row["latency_seconds"] for row in future_usage)},
                "evidence_status":"actual_usage","source_trace_sha256":hashlib.sha256(raw).hexdigest()})
    if not samples:
        raise ValueError("BLOCKED_no_real_v04_development_continuations")
    return samples
