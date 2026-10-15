"""Opt-in DeepSeek + official ARE single-agent read-only smoke run."""
from __future__ import annotations

import argparse
import json
import os

from .are_adapter import import_official_scenario, official_runner
from .are_bridge import ObservedContext, make_guarded_agent_builder
from .config import ROOT, load_config
from .controller import Controller
from .deepseek import API_BASE, MODEL, PROVIDER, DeepSeekClient, DeepSeekEngineBuilder, RunBudget
from .trace import TraceLogger


def main(mode: str = "real_are_single"):
    if mode not in {"real_are_single", "real_are_multi"}:
        raise ValueError("Unknown ARE execution mode")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="scenario_universe_22_0421h2")
    parser.add_argument("--allow-read-tool", action="append", default=[])
    parser.add_argument("--allow-read-app", action="append", default=["Files"])
    parser.add_argument("--max-requests", type=int, default=12 if mode == "real_are_multi" else 4)
    parser.add_argument("--max-total-tokens", type=int, default=100000)
    parser.add_argument("--max-cost-usd", type=float, default=0.20 if mode == "real_are_multi" else 0.05)
    parser.add_argument("--acknowledge-model-cost", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_model_cost:
        parser.error("A real model call requires --acknowledge-model-cost")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("DEEPSEEK_API_KEY is not set")
    if args.max_requests < 1 or args.max_total_tokens < 1 or args.max_cost_usd <= 0:
        parser.error("Budgets must be positive")
    config = load_config()
    record = config.select(args.scenario)
    scenario = import_official_scenario(record, config.are_root)
    trace = TraceLogger(ROOT / "results" / "are" / f"{record.scenario_id}_{mode}.jsonl",
                        mode=mode)
    trace.log("run_started", scenario_id=record.scenario_id,
              model=MODEL, provider=PROVIDER, api_base=API_BASE,
              max_requests=args.max_requests, max_total_tokens=args.max_total_tokens,
              max_cost_usd=args.max_cost_usd)
    controller = Controller(config, trace, frozenset(), frozenset())
    task_evidence = controller.provenance.add(
        "startup", "low_trust", "ARE task will arrive through official notification system")
    controller.update(task_evidence.text, (), "intake")
    context = ObservedContext(controller, task_evidence.evidence_id)
    budget = RunBudget(trace, args.max_requests, args.max_total_tokens, args.max_cost_usd,
                       mode=mode)
    controller.run_budget = budget
    client = DeepSeekClient(budget)
    builder = make_guarded_agent_builder(
        controller, context, DeepSeekEngineBuilder(client, context.record_model_input),
        frozenset(args.allow_read_app), frozenset(args.allow_read_tool),
        dynamic_client=client if mode == "real_are_multi" else None)
    from are.simulation.scenarios.config import ScenarioRunnerConfig

    runner_config = ScenarioRunnerConfig(
        agent="default", model=MODEL, model_provider=PROVIDER,
        endpoint=API_BASE, oracle=False, export=True, max_turns=1,
        output_dir=str(ROOT / "results" / "are"),
    )
    result = official_runner(builder).run(runner_config, scenario)
    print(json.dumps({"scenario_id": record.scenario_id, "success": result.success,
                      "mode": mode, "read_only": True,
                      "dynamic_team_executed": mode == "real_are_multi" and
                      controller.spec.version >= 4,
                      "organization_version": controller.spec.version,
                      "official_trace": result.export_path,
                      "error_type": type(result.exception).__name__ if result.exception else None,
                      "model_requests": budget.requests, "input_tokens": budget.input_tokens,
                      "output_tokens": budget.output_tokens,
                      "latency_seconds": budget.latency_seconds,
                      "peak_cost_estimate_usd": budget.peak_cost_estimate_usd},
                     ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
