from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ROOT, load_config
from .controller import Controller
from .security import Message, TaskAuthorization, ToolDefinition
from .trace import TraceLogger


def run_demo(scenario_id: str, trace_path: Path) -> dict:
    config = load_config()
    record = config.select(scenario_id)
    trace = TraceLogger(trace_path)
    trace.log("scenario_selected", scenario_id=record.scenario_id,
              sha256=record.sha256, mode="offline_mock")
    calls = []

    def mock_read(key: str):
        calls.append(("read", key))
        return f"mock data for {key}"

    def mock_write(value: str):
        calls.append(("write", value))
        return f"saved: {value}"

    controller = Controller(config, trace,
                            frozenset({"mock_read", "mock_write"}),
                            frozenset({"mock_resource"}))
    controller.tools.register(ToolDefinition(
        "mock_read", "read", frozenset({"mock_resource"}), mock_read))
    controller.tools.register(ToolDefinition(
        "mock_write", "write", frozenset({"mock_resource"}), mock_write))
    task_text = json.dumps({"task": "Collect, verify, save",
                            "authorized_actions": [{
                                "tool": "mock_write", "operation": "write",
                                "resource": "mock_resource", "arguments": {"value": "approved"},
                                "recipient_field": None, "recipient": None, "phase": "commit"}]})
    task = controller.provenance.add("user_task", "trusted_task", task_text)
    stages = []
    stages.append(controller.update(task.text, (), "intake").pattern)
    stages.append(controller.update(task.text, ("collect A", "collect B"), "parallel").pattern)
    output, observed = controller.tools.call(
        controller.spec, "agent_0", controller.spec.version, "mock_read",
        "mock_resource", {"key": "A"}, task.evidence_id)
    controller.messages.send(controller.spec, Message(
        "agent_0", "agent_1", controller.spec.version,
        observed.evidence_id, observed.text))
    delivered_to_worker = controller.messages.deliver(controller.spec)
    summary = controller.provenance.add(
        "agent_1_summary", "derived", "A collected", (observed.evidence_id,),
        owner_agent="agent_1")
    controller.messages.send(controller.spec, Message(
        "agent_1", "agent_0", controller.spec.version,
        summary.evidence_id, summary.text))
    delivered_to_coordinator = controller.messages.deliver(controller.spec)
    stages.append(controller.update(task.text, (), "commit", 1).pattern)
    try:
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "mock_write", "mock_resource", {"value": "bad"},
                              summary.evidence_id)
    except PermissionError:
        pass
    stages.append(controller.safe_reconfigure(task.text, (), 1).pattern)
    # Explicit task intent, rather than the untrusted tool output, authorizes
    # this one mock write. The low-trust content remains in the audit graph.
    controller.authorization.grant(TaskAuthorization(
        "agent_0", controller.spec.version, "mock_write", "write", "mock_resource",
        None, None, {"value": "approved"}, "commit", task.evidence_id))
    controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                          "mock_write", "mock_resource", {"value": "approved"},
                          task.evidence_id)
    result = {"scenario_id": scenario_id, "mode": "offline_mock",
              "topology_sequence": stages,
              "messages_delivered": len(delivered_to_worker) + len(delivered_to_coordinator),
              "mock_calls": calls, "final_version": controller.spec.version,
              "trace": str(trace_path)}
    trace.log("demo_finished", **result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--trace", type=Path, default=ROOT / "results" / "demo.jsonl")
    args = parser.parse_args()
    print(json.dumps(run_demo(args.scenario, args.trace), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
