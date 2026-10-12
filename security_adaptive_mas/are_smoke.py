"""Opt-in, read-only ARE runtime smoke path; this may incur model API charges."""
from __future__ import annotations

import argparse
import json

from .are_adapter import import_official_scenario, official_runner
from .are_bridge import make_guarded_agent_builder
from .config import ROOT, load_config
from .controller import Controller
from .trace import TraceLogger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--allow-read-tool", action="append", default=[])
    parser.add_argument("--allow-app", action="append", default=[])
    parser.add_argument("--acknowledge-model-cost", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_model_cost:
        parser.error("A real model call requires --acknowledge-model-cost")
    config = load_config()
    record = config.select(args.scenario)
    scenario = import_official_scenario(record, config.are_root)
    trace = TraceLogger(ROOT / "results" / "are" / f"{record.scenario_id}_controller.jsonl")
    controller = Controller(config, trace, frozenset(args.allow_read_tool),
                            frozenset(args.allow_app))
    task_evidence = controller.provenance.add(
        "startup", "trusted_task", "ARE task will arrive through official notification system")
    controller.update(task_evidence.text, (), "intake")
    builder = make_guarded_agent_builder(controller, task_evidence.evidence_id)
    from are.simulation.scenarios.config import ScenarioRunnerConfig

    runner_config = ScenarioRunnerConfig(
        agent="default", model=args.model, model_provider=args.provider,
        endpoint=args.endpoint, oracle=False, export=True, max_turns=1,
        output_dir=str(ROOT / "results" / "are"),
    )
    result = official_runner(builder).run(runner_config, scenario)
    print(json.dumps({"scenario_id": record.scenario_id, "success": result.success,
                      "mode": "are_read_only_smoke", "official_trace": result.export_path},
                     ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
