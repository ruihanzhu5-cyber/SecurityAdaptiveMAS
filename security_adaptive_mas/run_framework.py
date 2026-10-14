"""Official ARE full episodes, shared budgets, immutable per-run evidence."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time
import traceback
from uuid import uuid4

from .are_adapter import import_official_scenario, official_runner
from .are_bridge import ObservedContext, make_framework_agent_builder
from .attack import evaluate_attack, load_attack_spec
from .config import ROOT, ScenarioRecord, load_config
from .dataset_store import export_scenario
from .controller import Controller
from .deepseek import (API_BASE, MODEL, PROVIDER, DeepSeekClient, DeepSeekSettings,
                       RunBudget, PEAK_INPUT_USD_PER_TOKEN, PEAK_OUTPUT_USD_PER_TOKEN)
from .security import SandboxPermissivePolicy, TaskScopePolicy, UnsupportedTaskScope
from .trace import TraceLogger


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def code_state():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--", "security_adaptive_mas", "tests", "configs"], cwd=ROOT)
    hashes = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for folder in ("security_adaptive_mas", "configs") for path in sorted((ROOT / folder).rglob("*"))
              if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".md", ".txt"}}
    status = subprocess.check_output(["git","status","--porcelain"],cwd=ROOT).decode()
    return {"code_revision": revision, "dirty_diff_hash": hashlib.sha256(diff).hexdigest(),
            "dirty_status":status,
            "source_file_hashes": hashes,
            "code_snapshot_id": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def ledger_charge(path):
    if not path.exists():
        return 0.0
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        latest[row["ledger_id"]] = row
    return sum(row["cost_estimate_usd"] + row["unknown_cost_bound"] for row in latest.values())


def run_case(args, config, scenario_id, batch_dir, frozen, attack_spec=None):
    if code_state() != frozen:
        raise RuntimeError("Code changed after batch freeze; do not mix revisions")
    record = config.select(scenario_id)
    data_record = next((row for row in args.design_manifest["records"] if row["scenario_id"] == scenario_id),None) if args.design else None
    if data_record and data_record["sha256"] != record.sha256:
        raise ValueError("frozen_manifest_scenario_hash_mismatch")
    applies = attack_spec is not None and attack_spec.target_case == scenario_id
    policy_version = (SandboxPermissivePolicy.version if args.task_scope_policy == "sandbox_permissive"
                      else TaskScopePolicy.version)
    run_id = uuid4().hex[:12]
    out = batch_dir / scenario_id / run_id
    out.mkdir(parents=True, exist_ok=False)
    trace = TraceLogger(out / "framework_trace.jsonl", mode="real_are_multi" if args.mode == "adaptive" else "real_are_single")
    budget = RunBudget(trace, args.max_requests, args.max_total_tokens, args.max_cost_usd, trace.mode)
    settings = DeepSeekSettings(max_attempts=1) if args.design else DeepSeekSettings()
    if args.design:
        budget.purpose_limits = args.design["subbudgets"]
        budget.input_rate = args.design["rates"]["input_usd_per_token"]
        budget.output_rate = args.design["rates"]["output_usd_per_token"]
    client = DeepSeekClient(budget, settings)
    controller = Controller(config, trace, frozenset(), frozenset(),
                            monitor_enabled=True, task_llm_enabled=True,
                            max_analysis_requests=args.design["subbudgets"]["task_analysis"] if args.design else 3)
    controller.design = args.design
    controller.run_budget = budget
    controller.organization_policy = args.organization_policy or (
        "joint" if args.risk_aware == "on" else "task_only")
    if args.design:
        controller.organization_policy = args.design["method"]
    controller.risk_aware_enabled = controller.organization_policy in {
        "joint", "reactive_isolation", "task_plus_filter"}
    initial = controller.provenance.add("startup", "low_trust", "Waiting for actual delivered USER/ENV notifications")
    context = ObservedContext(controller, initial.evidence_id)
    source_versions = json.loads((ROOT / "configs/source_versions.json").read_text(encoding="utf-8"))
    run_config = {"scenario_id": scenario_id, "run_id": run_id,
        "split": data_record["split"] if data_record else "gaia2_validation_unreviewed" if args.dataset_category else "development",
        "data_manifest_hash":args.design_manifest_hash,
        "dataset_category": args.dataset_category,
        "dataset_row_index_zero_based": args.dataset_row_index,
        "design":args.design,"design_hash":args.design_hash,
        "mode": args.mode, "attack_mode": args.attack,
        "attack_payload_hash": attack_spec.payload_hash if applies else None,
        "attack_id": attack_spec.attack_id if applies else None,
        "risk_aware": args.risk_aware,
        "effective_risk_selection": controller.risk_aware_enabled,
        "organization_policy": controller.organization_policy,
        "task_scope_policy": args.task_scope_policy, **frozen,
        "source_versions": source_versions, "scenario_hash": record.sha256,
        "policy_version": policy_version, "model_id": MODEL, "provider": PROVIDER,
        "api_base": API_BASE, "settings": asdict(settings), "execution_mode": "sequential",
        "max_turns": args.max_turns, "max_requests": args.max_requests,
        "max_total_tokens": args.max_total_tokens, "max_cost_usd": args.max_cost_usd,
        "max_agents": config.max_agents,
        "max_task_analysis_requests":args.design["subbudgets"]["task_analysis"] if args.design else 3,
        "max_reconfigurations":args.design["reconfiguration_limit"] if args.design else 12,
        "worker_step_limit":args.design["role_step_limit"] if args.design else 3,
        "max_tool_calls_per_worker_step": 4,
        "oracle": False, "load_completed_events": False,
        "rate_version": "existing-project-estimate-2026-10-12-not-current-bill",
        "input_usd_per_token":budget.input_rate,
        "output_usd_per_token":budget.output_rate}
    save(out / "run_config.json", run_config)
    result = None
    scenario = None
    error_type = None
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    runner_log = out / "runner.log"
    handler = logging.FileHandler(runner_log, encoding="utf-8")
    logging.getLogger().addHandler(handler)
    with runner_log.open("a", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
        try:
            scenario = import_official_scenario(record, config.are_root)
            builder = make_framework_agent_builder(controller, context, client, args.mode,
                task_scope_policy=args.task_scope_policy, attack_spec=attack_spec if applies else None)
            from are.simulation.scenarios.config import ScenarioRunnerConfig
            runner_config = ScenarioRunnerConfig(agent="default", model=MODEL, model_provider=PROVIDER,
                endpoint=API_BASE, oracle=False, export=True, max_turns=args.max_turns,
                output_dir=str(out / "official_trace"))
            result = official_runner(builder).run(runner_config, scenario)
            error_type = type(result.exception).__name__ if result.exception else None
        except Exception as error:
            error_type = type(error).__name__
            trace.log("episode_failed", exception_type=error_type, reason=str(error))
            if args.design:
                save(out / "failure.json", {"exception_type":error_type,"reason":str(error),
                                             "traceback":traceback.format_exc(),"status":"failed"})
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
    team = getattr(controller, "framework_team", None)
    terminal = team.terminal_error if team else None
    unsupported = isinstance(terminal, UnsupportedTaskScope)
    if terminal:
        error_type = type(terminal).__name__
    if args.design and error_type and not (out / "failure.json").exists():
        exception = terminal or (result.exception if result is not None else None)
        save(out / "failure.json", {"exception_type":error_type,"reason":str(exception),
            "traceback":"".join(traceback.format_exception(exception)) if exception is not None else None,
            "status":"failed"})
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    decisions = [event for event in events if event["kind"] == "controller_runtime_decision"]
    joint_decisions = [event for event in events if event["kind"] == "joint_decision"]
    topology = [event for event in events if event["kind"] in {"topology_patch_applied", "worker_started", "worker_output_ready", "worker_output_withheld", "runtime_object_delivered", "message_delivered"}]
    for name, rows in (("controller_decisions.jsonl", decisions),
                       ("joint_decisions.jsonl", joint_decisions),
                       ("topology_events.jsonl", topology)):
        (out / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    tools, denied = {}, {}
    for event in events:
        if event["kind"] == "tool_started":
            tools[event["agent_id"]] = tools.get(event["agent_id"], 0) + 1
        if event["kind"] == "tool_denied":
            denied[event["reason"]] = denied.get(event["reason"], 0) + 1
    exhausted = any(event["kind"] == "budget_exhausted" for event in events)
    official_success = result.success if result is not None else None
    validation_available = result is not None and (result.exception is None or result.success is True)
    runtime = team.runtime if team else None
    summary = {**run_config, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "wall_time_seconds": time.monotonic() - start,
        "scenario_success": official_success if validation_available else None,
        "official_runner_success": official_success, "validation_available": validation_available,
        "validation_status": "UNVERIFIED",
        "legitimate_task_completed": "unknown", "semantic_verified": "unknown",
        "exception_type": error_type,
        "stop_reason": "unsupported_task_scope" if unsupported else "budget_exhausted" if exhausted else "runner_exception" if error_type else "official_episode_end",
        "agent_turns": team.turns if team else 0, "worker_steps": team.worker_steps if team else 0,
        "controller_decisions": len(decisions), "initial_agent_count": 1,
        "max_active_agents": runtime.max_active if runtime else 1,
        "distinct_agent_instances": sorted(runtime.instances) if runtime else ["agent_0"],
        "patches_proposed": sum(len(row["candidates"]) for row in decisions),
        "patches_applied": runtime.applied if runtime else 0,
        "effective_reconfigurations": len({event["graph_version"] for event in events if event["kind"] == "worker_started"}),
        "effective_graph_patches": sum(event["graph_version"] != event["previous_version"]
            for event in events if event["kind"] == "topology_patch_applied"),
        "dynamic_execution_observed": any(key.startswith("worker:") for key in budget.requests_by_purpose),
        "modules_not_exercised": ([] if args.design and joint_decisions else ["temporal_projection"]) + ["reuse_worker"]
            + ([] if any(event["kind"] == "attack_injected_delivered" for event in events)
               else ["attack_injection"])
            + ([] if applies else ["attack_scoring"])
            + ([] if team and team.worker_steps else ["worker_execution", "retirement"]),
        "model_requests_total": budget.requests, "requests_by_purpose": budget.requests_by_purpose,
        "model_requests_submitted": sum(event["kind"] == "model_request_submitted" for event in events),
        "actual_usage_by_purpose": budget.actual_usage_by_purpose,
        "input_tokens_total": budget.input_tokens, "output_tokens_total": budget.output_tokens,
        "cost_estimate_usd": budget.peak_cost_estimate_usd, "unknown_cost_bound": budget.unknown_cost_bound,
        "budget_exhausted": exhausted, "unaccounted_usage_notes": budget.unaccounted_usage_notes,
        "tool_calls_by_agent": tools, "blocked_actions_by_reason": denied,
        "risk_model":"activation_ordered_history_and_future_projection" if args.design else "current_effective_graph_with_live_objects",
        "reachability_proxy": joint_decisions[-1]["risk"] if joint_decisions else None,
        "risk_events_observed": team.risk_events if team else [],
        "legitimate_task_status": "unknown",
        "controller_overhead": {"actual_task_analysis": budget.actual_usage_by_purpose.get("task_analysis", {
            "requests": 0, "input_tokens": 0, "output_tokens": 0,
            "latency_seconds": 0.0, "cost_estimate_usd": 0.0}),
            "rewrite_model_requests": 0,
            "future_cost_estimator": "unknown"},
        "attack_success": None, "containment_success": None,
        "trace_paths": {"framework": str(trace.path), "official": str(result.export_path) if result is not None else None, "runner": str(runner_log)},
        "official_trace_hashes": {path.relative_to(out).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (out / "official_trace").rglob('*') if path.is_file()}}
    if applies:
        summary["attack_scoring"] = evaluate_attack(events, spec=attack_spec,
            official_success=official_success, stop_reason=summary["stop_reason"],
            require_submitted=bool(args.design),
            final_state=(email_final_state(scenario) if attack_spec.target_email_id and scenario is not None else None))
        summary["attack_delivered"] = summary["attack_scoring"]["attack_delivered"]
        summary["target_attempted"] = summary["attack_scoring"]["target_attempted"]
        summary["target_achieved"] = summary["attack_scoring"]["target_achieved"]
        summary["gateway_blocked"] = summary["attack_scoring"]["blocked_by_gateway"]
        summary["exposure_count"] = len(summary["attack_scoring"]["exposed_instances"])
    outcome = summary.get("target_achieved")
    summary["attack_target_achieved"] = (True if outcome is True or outcome == "true" else
                                         False if outcome is False or outcome == "false" else "unknown")
    save(out / "summary.json", summary)
    save(out / "evaluation.json", {key:summary.get(key, "unknown") for key in (
        "official_runner_success", "validation_available", "validation_status", "stop_reason",
        "legitimate_task_completed", "semantic_verified", "attack_scoring")})
    return summary


def email_final_state(scenario):
    """Inspect the already-run ARE app after execution, outside the online Controller."""
    folders = scenario.get_app("Emails").get_state()["folders"]
    return {"inbox_email_ids": [row["email_id"] for row in folders["INBOX"]["emails"]],
            "trash_email_ids": [row["email_id"] for row in folders["TRASH"]["emails"]]}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("single", "adaptive"), default="adaptive")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--suite", choices=("five",))
    selection.add_argument("--scenario")
    parser.add_argument("--dataset-category", help="With --scenario, select from a pinned full Gaia2 category")
    parser.add_argument("--design-config",type=Path,help="Explicit v0.4 research configuration")
    parser.add_argument("--attack", choices=("none", "single_injection"), default="none")
    parser.add_argument("--attack-spec", type=Path,
                        default=ROOT / "configs" / "attack_single_injection_22_kb5ah9.json")
    parser.add_argument("--attack-case",type=Path,help="v0.4 declared surface/family/evaluator metadata")
    parser.add_argument("--task-scope-policy", choices=("strict", "sandbox_permissive"), default="strict")
    parser.add_argument("--risk-aware", choices=("off", "on"), default="off")
    parser.add_argument("--organization-policy", choices=("joint", "task_only",
        "task_plus_filter", "reactive_isolation"),
        help="Use one shared graph runtime and gateway with a selected organization rule")
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-total-tokens", type=int, default=800000)
    parser.add_argument("--max-cost-usd", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results/framework_v0_1")
    parser.add_argument("--acknowledge-model-cost", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume-batch", type=Path, help="Reuse inspected first-case results only with identical code and run limits")
    parser.add_argument("--batch-cap-usd", type=float, default=5.0,
                        help="Shared output-ledger cap; does not replace per-episode limits")
    return parser


def main(argv=None, *, design_override=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.design, args.design_hash = None, None
    args.design_manifest, args.design_manifest_hash = None, None
    if args.design_config:
        from .organization_contracts import load_design
        args.design = load_design(args.design_config)
        if design_override is not None:
            from .organization_contracts import load_design_value
            args.design = load_design_value(design_override)
        args.design_hash = hashlib.sha256(json.dumps(args.design,sort_keys=True).encode()).hexdigest()
        manifest_path = ROOT/args.design["data_manifest"]
        args.design_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        args.design_manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if args.organization_policy is not None or args.risk_aware != "off":
            parser.error("v0.4 method is declared in --design-config; do not mix legacy policy flags")
    if not (1 <= args.max_turns <= 20 and 1 <= args.max_requests <= 100 and
            1 <= args.max_total_tokens <= 800000 and 0 < args.max_cost_usd <= 1 and
            0 < args.batch_cap_usd <= 5):
        parser.error("v0.1 limits: 20 turns, 100 requests, 800000 tokens, 1 USD per episode")
    config = load_config()
    if args.design:
        config = replace(config,max_agents=args.design["max_agents"])
    args.dataset_row_index = None
    if args.dataset_category:
        if not args.scenario:
            parser.error("--dataset-category requires --scenario")
        if args.dry_run:
            from .dataset_store import list_scenarios
            ids_in_category = list_scenarios(args.dataset_category)
            if args.scenario not in ids_in_category:
                parser.error("scenario not found in pinned category")
            args.dataset_row_index = ids_in_category.index(args.scenario)
        else:
            path, digest, args.dataset_row_index = export_scenario(args.dataset_category, args.scenario)
            record = ScenarioRecord(args.scenario, path, path.parent, digest)
            config = replace(config, scenario_ids=(args.scenario,), records={args.scenario: record})
    ids = config.scenario_ids if args.suite else (args.scenario,)
    if args.design and not set(ids) <= {row["scenario_id"] for row in args.design_manifest["records"]}:
        parser.error("v0.4 research run must belong to the explicit data manifest")
    if not (args.dry_run and args.dataset_category):
        for scenario_id in ids:
            config.select(scenario_id)
    attack_spec = None
    if args.attack == "single_injection":
        if args.attack_case:
            from .attack import AttackCase
            attack_spec = AttackCase.load(args.attack_case).overlay_spec()
        else:
            attack_spec = load_attack_spec(args.attack_spec)
        if args.suite or args.scenario != attack_spec.target_case:
            parser.error("single_injection requires its one declared --scenario")
    frozen = code_state()
    if args.dry_run:
        effective_policy = args.design["method"] if args.design else args.organization_policy or (
            "joint" if args.risk_aware == "on" else "task_only")
        preview = {"dry_run": True, "case_ids": ids, "attack": args.attack,
                          "design":args.design,"design_hash":args.design_hash,
                          "dataset_category": args.dataset_category,
                          "dataset_row_index_zero_based": args.dataset_row_index,
                          "attack_spec_hash": attack_spec.payload_hash if attack_spec else None,
                          "task_scope_policy": args.task_scope_policy, "risk_aware": args.risk_aware,
                          "effective_risk_selection": effective_policy in {"joint","fixed_security","reactive_isolation","task_plus_filter"},
                          "organization_policy": effective_policy,
                          "model_id":MODEL,"mode":args.mode,
                          "max_turns":args.max_turns,"max_requests":args.max_requests,
                          "max_total_tokens":args.max_total_tokens,"max_cost_usd":args.max_cost_usd,
                          "batch_cap_usd":args.batch_cap_usd,
                          "key_present": bool(os.environ.get("DEEPSEEK_API_KEY")), **frozen}
        print(json.dumps(preview,indent=2))
        return preview
    if not args.acknowledge_model_cost or not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("Real runs require acknowledgment and DEEPSEEK_API_KEY in process environment")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    ledger = output / "v0_1_cost_ledger.jsonl"
    batch_dir = args.resume_batch.resolve() if args.resume_batch else output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:6])
    batch_id = batch_dir.name
    summaries = []
    if args.resume_batch:
        previous = json.loads((batch_dir / "batch_config.json").read_text(encoding="utf-8"))
        if previous["code"] != frozen or batch_dir.parent != output:
            raise RuntimeError("Resume requires identical frozen code and output ledger")
        summaries = json.loads((batch_dir / "batch_results.json").read_text(encoding="utf-8"))
        for row in summaries:
            if (row["mode"] != args.mode or row["attack_mode"] != args.attack or
                row.get("dataset_category") != args.dataset_category or
                row.get("design_hash") != args.design_hash or
                row.get("task_scope_policy", "strict") != args.task_scope_policy or
                row.get("risk_aware", "off") != args.risk_aware or
                row.get("organization_policy") != (args.organization_policy or (
                    "joint" if args.risk_aware == "on" else "task_only")) or
                row.get("attack_payload_hash") != (attack_spec.payload_hash if attack_spec else None) or
                row["max_turns"] != args.max_turns or row["max_requests"] != args.max_requests or
                row["max_total_tokens"] != args.max_total_tokens or row["max_cost_usd"] != args.max_cost_usd):
                raise RuntimeError("Resume requires identical policy and experiment settings")
    save(batch_dir / "batch_config.json", {"case_ids": ids, "code": frozen, "batch_cap_usd": args.batch_cap_usd,
        "dataset_category": args.dataset_category,
        "design_hash":args.design_hash,"data_manifest_hash":args.design_manifest_hash,
        "attack": args.attack, "attack_payload_hash": attack_spec.payload_hash if attack_spec else None,
        "task_scope_policy": args.task_scope_policy, "risk_aware": args.risk_aware,
        "organization_policy": args.organization_policy or (
            "joint" if args.risk_aware == "on" else "task_only")})
    # A copy of each source fixes uncommitted and newly-added implementation too.
    for relative in frozen["source_file_hashes"]:
        dest = batch_dir / "code_snapshot" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / relative).read_bytes())
    for scenario_id in ids:
        if any(row["scenario_id"] == scenario_id for row in summaries):
            print("REUSE_INSPECTED", scenario_id, flush=True)
            continue
        if ledger_charge(ledger) + args.max_cost_usd > args.batch_cap_usd:
            save(batch_dir / (scenario_id + "_not_run.json"), {"scenario_id": scenario_id, "status": "blocked_batch_budget"})
            print(scenario_id, "BLOCKED batch budget", flush=True)
            continue
        print("START", scenario_id, batch_id, flush=True)
        ledger_id = batch_id + ":" + scenario_id
        with ledger.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({"ledger_id": ledger_id, "status": "reserved_before_episode",
                "cost_estimate_usd": args.max_cost_usd, "unknown_cost_bound": 0}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        summary = run_case(args, config, scenario_id, batch_dir, frozen, attack_spec)
        with ledger.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({"ledger_id": ledger_id, "status": "settled", "batch_id": batch_id, "run_id": summary["run_id"], "scenario_id": scenario_id,
                "cost_estimate_usd": summary["cost_estimate_usd"], "unknown_cost_bound": summary["unknown_cost_bound"]}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        summaries.append(summary)
        save(batch_dir / "batch_results.json", summaries)
        if args.design and summary["exception_type"]:
            raise RuntimeError("v0.4 episode failed; summary and costs retained: "+summary["exception_type"])
        print("FINISH", scenario_id, "success=", summary["scenario_success"], "stop=", summary["stop_reason"],
              "requests=", summary["model_requests_total"], "cost=", summary["cost_estimate_usd"], flush=True)
    print("BATCH_RESULTS", batch_dir / "batch_results.json", flush=True)
    return {"batch_dir":str(batch_dir),"summaries":summaries,"ledger":str(ledger)}


if __name__ == "__main__":
    main()
