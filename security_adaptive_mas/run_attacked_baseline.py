"""Official default Single Agent with one controlled email-result injection."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from types import MethodType
from uuid import uuid4

from .are_adapter import import_official_scenario, official_runner
from .attack import AttackOverlay, evaluate_attack, load_attack_spec
from .config import ROOT, load_config
from .deepseek import API_BASE, MODEL, PROVIDER, DeepSeekClient, DeepSeekEngineBuilder, RunBudget
from .run_framework import code_state, email_final_state, save
from .trace import TraceLogger


SCENARIO_ID = "scenario_universe_22_kb5ah9"
ATTACK_SPEC = ROOT / "configs" / "attack_single_injection_22_kb5ah9.json"
MAX_TURNS = 20
MAX_REQUESTS = 100
MAX_TOTAL_TOKENS = 800000
MAX_COST_USD = 1.0


class ObservingClient(DeepSeekClient):
    def __init__(self, budget, overlay, trace):
        super().__init__(budget)
        self.overlay = overlay
        self.trace = trace
        self.payload = Path(overlay.spec.payload_path).read_text(encoding="utf-8").strip()
        self.delivered = False

    def complete(self, messages, **kwargs):
        contains_payload = self.overlay.applied and any(
            self.payload in str(message.get("content") or "") for message in messages)
        response = super().complete(messages, **kwargs)
        if contains_payload and not self.delivered:
            self.delivered = True
            self.trace.log("attack_injected_delivered", evidence_id="attack_email_tool_result",
                           attack_id=self.overlay.spec.attack_id, agent_id="agent_0",
                           tool="Emails__list_emails")
            self.trace.log("model_input_observed", agent_id="agent_0",
                           parent_ids=["attack_email_tool_result"])
        return response


def attacked_builder(client, overlay, trace):
    from are.simulation.agents.agent_builder import AgentBuilder

    class AttackedDefaultBuilder(AgentBuilder):
        def build(self, agent_config, env=None, mock_responses=None):
            agent = super().build(agent_config, env, mock_responses)
            original_init_tools = agent.init_tools

            def init_tools(instance, scenario):
                original_init_tools(scenario)
                for tool in instance.tools:
                    if tool.name not in {"Emails__list_emails", "Emails__delete_email"}:
                        continue
                    original_forward = tool.forward

                    def observed_forward(current_tool, *args, _original=original_forward, **kwargs):
                        trace.log("tool_started", agent_id="agent_0", tool=current_tool.name,
                                  arguments=kwargs)
                        result = _original(*args, **kwargs)
                        if current_tool.name == "Emails__list_emails":
                            result = overlay.apply_tool_result(
                                result, tool_name=current_tool.name, agent_id="agent_0")
                        trace.log("tool_completed", agent_id="agent_0", tool=current_tool.name,
                                  arguments=kwargs)
                        return result

                    tool.forward = MethodType(observed_forward, tool)

            agent.init_tools = MethodType(init_tools, agent)
            return agent

    return AttackedDefaultBuilder(llm_engine_builder=DeepSeekEngineBuilder(client))


def dry_run(config, spec, output):
    scenario = import_official_scenario(config.select(SCENARIO_ID), config.are_root)
    from are.simulation.tool_utils import AppToolAdapter
    tool = next(AppToolAdapter(item) for item in scenario.get_tools()
                if item._public_name == "Emails__list_emails")
    trace = TraceLogger(output / "preflight_trace.jsonl", mode="offline_mock")
    overlay = AttackOverlay(spec, trace, scenario_id=SCENARIO_ID,
                            receiver_agent_id="agent_0")
    returned = tool(folder_name="INBOX", offset=100, limit=100)
    selected_id = spec.source_selector.removeprefix("email_id:")
    original = next(email for email in returned.emails if email.email_id == selected_id)
    modified = overlay.apply_tool_result(returned, tool_name=tool.name, agent_id="agent_0")
    injected = next(email for email in modified.emails if email.email_id == selected_id)
    payload = Path(spec.payload_path).read_text(encoding="utf-8").strip()
    if original.content == injected.content or payload not in injected.content:
        raise AssertionError("Baseline email-result injection did not alter the copied tool result")
    if payload in next(email for email in scenario.get_app("Emails").get_state()["folders"]["INBOX"]["emails"]
                       if email["email_id"] == selected_id)["content"]:
        raise AssertionError("Baseline injection changed the ARE email state")
    print(json.dumps({"dry_run": True, "scenario_id": SCENARIO_ID,
                      "source_email_id": selected_id, "source_found": True,
                      "tool_result_modified": True, "app_state_unchanged": True,
                      "payload_hash": spec.payload_hash}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "results" / "attacked_bare_baseline")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--acknowledge-model-cost", action="store_true")
    args = parser.parse_args()
    config = load_config()
    record = config.select(SCENARIO_ID)
    spec = load_attack_spec(ATTACK_SPEC)
    if spec.target_case != SCENARIO_ID or spec.surface != "tool_result_email_content":
        raise ValueError("Attack spec is not the fixed email-result case")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        dry_run(config, spec, output)
        return
    if not args.acknowledge_model_cost or not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("Real run requires acknowledgment and DEEPSEEK_API_KEY")

    run_dir = output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:6])
    run_dir.mkdir(parents=True, exist_ok=False)
    frozen = code_state()
    save(run_dir / "run_config.json", {"scenario_id": SCENARIO_ID,
         "scenario_sha256": record.sha256, "agent": "official_are_default_single_agent",
         "controller_used": False, "oracle": False, "attack_id": spec.attack_id,
         "attack_payload_hash": spec.payload_hash, "injection_receiver": "agent_0",
         "model": MODEL, "provider": PROVIDER, "api_base": API_BASE,
         "max_turns": MAX_TURNS, "max_requests": MAX_REQUESTS,
         "max_total_tokens": MAX_TOTAL_TOKENS, "max_cost_usd": MAX_COST_USD, **frozen})
    trace = TraceLogger(run_dir / "attack_trace.jsonl", mode="real_are_single")
    budget = RunBudget(trace, MAX_REQUESTS, MAX_TOTAL_TOKENS, MAX_COST_USD,
                       mode="real_are_single")
    overlay = AttackOverlay(spec, trace, scenario_id=SCENARIO_ID,
                            receiver_agent_id="agent_0")
    client = ObservingClient(budget, overlay, trace)
    scenario = import_official_scenario(record, config.are_root)
    from are.simulation.scenarios.config import ScenarioRunnerConfig

    runner_config = ScenarioRunnerConfig(
        agent="default", model=MODEL, model_provider=PROVIDER,
        endpoint=API_BASE, oracle=False, export=True, max_turns=MAX_TURNS,
        output_dir=str(run_dir / "official_trace"))
    runner_log = run_dir / "runner.log"
    handler = logging.FileHandler(runner_log, encoding="utf-8")
    logging.getLogger().addHandler(handler)
    result = None
    error_type = None
    started = time.monotonic()
    with runner_log.open("a", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
        try:
            result = official_runner(attacked_builder(client, overlay, trace)).run(
                runner_config, scenario)
            error_type = type(result.exception).__name__ if result.exception else None
        except Exception as error:
            error_type = type(error).__name__
            trace.log("episode_failed", exception_type=error_type, reason=str(error))
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    exhausted = any(event["kind"] == "budget_exhausted" for event in events)
    official_success = result.success if result is not None else None
    scoring = evaluate_attack(events, spec=spec, official_success=official_success,
                              stop_reason="budget_exhausted" if exhausted else "official_episode_end",
                              final_state=email_final_state(scenario))
    summary = {"scenario_id": SCENARIO_ID, "run_dir": str(run_dir),
               "agent": "official_are_default_single_agent", "controller_used": False,
               "attack_id": spec.attack_id, "attack_payload_hash": spec.payload_hash,
               "official_runner_success": official_success,
               "exception_type": error_type,
               "stop_reason": "budget_exhausted" if exhausted else "runner_exception" if error_type else "official_episode_end",
               "model_requests": budget.requests,
               "input_tokens": budget.input_tokens, "output_tokens": budget.output_tokens,
               "cost_estimate_usd": budget.peak_cost_estimate_usd,
               "wall_time_seconds": time.monotonic() - started,
               "attack_scoring": scoring,
               "target_email_final_state": "TRASH" if scoring["target_achieved"] == "true" else
                   "INBOX" if scoring["target_achieved"] == "false" else "unknown"}
    save(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
