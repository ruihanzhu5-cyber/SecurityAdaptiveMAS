"""Official ARE full episodes, shared budgets, immutable per-run evidence."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from .are_adapter import import_official_scenario, official_runner
from .are_bridge import ObservedContext, make_framework_agent_builder
from .config import ROOT, load_config
from .controller import Controller
from .deepseek import (API_BASE, MODEL, PROVIDER, DeepSeekClient, DeepSeekSettings,
                       RunBudget, PEAK_INPUT_USD_PER_TOKEN, PEAK_OUTPUT_USD_PER_TOKEN)
from .security import TaskScopePolicy, UnsupportedTaskScope
from .trace import TraceLogger


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def code_state():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--", "security_adaptive_mas", "tests"], cwd=ROOT)
    hashes = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((ROOT / "security_adaptive_mas").glob("*.py"))}
    return {"code_revision": revision, "dirty_diff_hash": hashlib.sha256(diff).hexdigest(),
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


def run_case(args, config, scenario_id, batch_dir, frozen):
    if code_state() != frozen:
        raise RuntimeError("Code changed after batch freeze; do not mix revisions")
    record = config.select(scenario_id)
    run_id = uuid4().hex[:12]
    out = batch_dir / scenario_id / run_id
    out.mkdir(parents=True, exist_ok=False)
    trace = TraceLogger(out / "framework_trace.jsonl", mode="real_are_multi" if args.mode == "adaptive" else "real_are_single")
    budget = RunBudget(trace, args.max_requests, args.max_total_tokens, args.max_cost_usd, trace.mode)
    settings = DeepSeekSettings()
    client = DeepSeekClient(budget, settings)
    controller = Controller(config, trace, frozenset(), frozenset(),
                            monitor_enabled=True, task_llm_enabled=True, max_analysis_requests=3)
    controller.run_budget = budget
    initial = controller.provenance.add("startup", "low_trust", "Waiting for actual delivered USER/ENV notifications")
    context = ObservedContext(controller, initial.evidence_id)
    source_versions = json.loads((ROOT / "configs/source_versions.json").read_text(encoding="utf-8"))
    run_config = {"scenario_id": scenario_id, "run_id": run_id, "split": "development",
        "mode": args.mode, "attack_mode": "none", **frozen,
        "source_versions": source_versions, "scenario_hash": record.sha256,
        "policy_version": TaskScopePolicy.version, "model_id": MODEL, "provider": PROVIDER,
        "api_base": API_BASE, "settings": asdict(settings), "execution_mode": "sequential",
        "max_turns": args.max_turns, "max_requests": args.max_requests,
        "max_total_tokens": args.max_total_tokens, "max_cost_usd": args.max_cost_usd,
        "max_agents": config.max_agents, "max_task_analysis_requests": 3,
        "max_reconfigurations": 12, "worker_step_limit": 3,
        "max_tool_calls_per_worker_step": 4,
        "oracle": False, "load_completed_events": False,
        "rate_version": "existing-project-estimate-2026-10-12-not-current-bill",
        "input_usd_per_token": PEAK_INPUT_USD_PER_TOKEN,
        "output_usd_per_token": PEAK_OUTPUT_USD_PER_TOKEN}
    save(out / "run_config.json", run_config)
    result = None
    error_type = None
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    runner_log = out / "runner.log"
    handler = logging.FileHandler(runner_log, encoding="utf-8")
    logging.getLogger().addHandler(handler)
    with runner_log.open("a", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
        try:
            scenario = import_official_scenario(record, config.are_root)
            builder = make_framework_agent_builder(controller, context, client, args.mode)
            from are.simulation.scenarios.config import ScenarioRunnerConfig
            runner_config = ScenarioRunnerConfig(agent="default", model=MODEL, model_provider=PROVIDER,
                endpoint=API_BASE, oracle=False, export=True, max_turns=args.max_turns,
                output_dir=str(out / "official_trace"))
            result = official_runner(builder).run(runner_config, scenario)
            error_type = type(result.exception).__name__ if result.exception else None
        except Exception as error:
            error_type = type(error).__name__
            trace.log("episode_failed", exception_type=error_type, reason=str(error))
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
    team = getattr(controller, "framework_team", None)
    terminal = team.terminal_error if team else None
    unsupported = isinstance(terminal, UnsupportedTaskScope)
    if terminal:
        error_type = type(terminal).__name__
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    decisions = [event for event in events if event["kind"] == "controller_runtime_decision"]
    topology = [event for event in events if event["kind"] in {"topology_patch_applied", "worker_started", "worker_receipt", "runtime_object_delivered", "message_delivered"}]
    for name, rows in (("controller_decisions.jsonl", decisions), ("topology_events.jsonl", topology)):
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
        "exception_type": error_type,
        "stop_reason": "unsupported_task_scope" if unsupported else "budget_exhausted" if exhausted else "runner_exception" if error_type else "official_episode_end",
        "agent_turns": team.turns if team else 0, "worker_steps": team.worker_steps if team else 0,
        "controller_decisions": len(decisions), "initial_agent_count": 1,
        "max_active_agents": runtime.max_active if runtime else 1,
        "distinct_agent_instances": sorted(runtime.instances) if runtime else ["agent_0"],
        "patches_proposed": sum(len(row["candidates"]) for row in decisions),
        "patches_applied": runtime.applied if runtime else 0,
        "effective_reconfigurations": len({event["graph_version"] for event in events if event["kind"] == "worker_started"}),
        "dynamic_execution_observed": any(key.startswith("worker:") for key in budget.requests_by_purpose),
        "modules_not_exercised": ["attack_injection", "attack_scoring", "temporal_projection", "reuse_worker"] + ([] if team and team.worker_steps else ["worker_execution", "retirement"]),
        "model_requests_total": budget.requests, "requests_by_purpose": budget.requests_by_purpose,
        "input_tokens_total": budget.input_tokens, "output_tokens_total": budget.output_tokens,
        "cost_estimate_usd": budget.peak_cost_estimate_usd, "unknown_cost_bound": budget.unknown_cost_bound,
        "budget_exhausted": exhausted, "unaccounted_usage_notes": budget.unaccounted_usage_notes,
        "tool_calls_by_agent": tools, "blocked_actions_by_reason": denied,
        "risk_model": "current_effective_graph_with_live_objects",
        "risk_events_observed": team.risk_events if team else [],
        "attack_success": None, "containment_success": None,
        "trace_paths": {"framework": str(trace.path), "official": str(result.export_path) if result is not None else None, "runner": str(runner_log)},
        "official_trace_hashes": {path.relative_to(out).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (out / "official_trace").rglob('*') if path.is_file()}}
    save(out / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("single", "adaptive"), default="adaptive")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--suite", choices=("five",))
    selection.add_argument("--scenario")
    parser.add_argument("--attack", choices=("none",), default="none")
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-total-tokens", type=int, default=800000)
    parser.add_argument("--max-cost-usd", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results/framework_v0_1")
    parser.add_argument("--acknowledge-model-cost", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume-batch", type=Path, help="Reuse inspected first-case results only with identical code and run limits")
    args = parser.parse_args()
    if not (1 <= args.max_turns <= 20 and 1 <= args.max_requests <= 100 and
            1 <= args.max_total_tokens <= 800000 and 0 < args.max_cost_usd <= 1):
        parser.error("v0.1 limits: 20 turns, 100 requests, 800000 tokens, 1 USD per episode")
    config = load_config()
    ids = config.scenario_ids if args.suite else (args.scenario,)
    for scenario_id in ids:
        config.select(scenario_id)
    frozen = code_state()
    if args.dry_run:
        print(json.dumps({"dry_run": True, "case_ids": ids, "key_present": bool(os.environ.get("DEEPSEEK_API_KEY")), **frozen}, indent=2))
        return
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
                row["max_turns"] != args.max_turns or row["max_requests"] != args.max_requests or
                row["max_total_tokens"] != args.max_total_tokens or row["max_cost_usd"] != args.max_cost_usd):
                raise RuntimeError("Resume requires identical policy and experiment settings")
    save(batch_dir / "batch_config.json", {"case_ids": ids, "code": frozen, "batch_cap_usd": 5, "attack": "none"})
    # A copy of each source fixes uncommitted and newly-added implementation too.
    for relative in frozen["source_file_hashes"]:
        dest = batch_dir / "code_snapshot" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / relative).read_bytes())
    for scenario_id in ids:
        if any(row["scenario_id"] == scenario_id for row in summaries):
            print("REUSE_INSPECTED", scenario_id, flush=True)
            continue
        if ledger_charge(ledger) + args.max_cost_usd > 5:
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
        summary = run_case(args, config, scenario_id, batch_dir, frozen)
        with ledger.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({"ledger_id": ledger_id, "status": "settled", "batch_id": batch_id, "run_id": summary["run_id"], "scenario_id": scenario_id,
                "cost_estimate_usd": summary["cost_estimate_usd"], "unknown_cost_bound": summary["unknown_cost_bound"]}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        summaries.append(summary)
        save(batch_dir / "batch_results.json", summaries)
        print("FINISH", scenario_id, "success=", summary["scenario_success"], "stop=", summary["stop_reason"],
              "requests=", summary["model_requests_total"], "cost=", summary["cost_estimate_usd"], flush=True)
    print("BATCH_RESULTS", batch_dir / "batch_results.json", flush=True)


if __name__ == "__main__":
    main()
