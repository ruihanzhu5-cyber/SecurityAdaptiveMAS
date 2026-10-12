import json
from uuid import uuid4

import pytest

from security_adaptive_mas.config import ROOT, load_config
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.demo import run_demo
from security_adaptive_mas.organization import communication_edges
from security_adaptive_mas.security import Message, ToolDefinition
from security_adaptive_mas.trace import TraceLogger


@pytest.fixture
def system():
    cfg = load_config()
    controller = Controller(cfg, TraceLogger(ROOT / "work" / "test_logs" / f"{uuid4().hex}.jsonl"),
                            frozenset({"read", "write"}), frozenset({"resource"}))
    writes = []
    controller.tools.register(ToolDefinition("read", "read", frozenset({"resource"}),
                                             lambda: "external content"))
    controller.tools.register(ToolDefinition("write", "write", frozenset({"resource"}),
                                             lambda value: writes.append(value)))
    task = controller.provenance.add("user", "trusted_task", "do task")
    controller.update(task.text, ("a", "b"), "parallel")
    return controller, task, writes


def test_fixed_config_and_hashes():
    config = load_config()
    assert len(config.scenario_ids) == 5
    for scenario_id in config.scenario_ids:
        assert config.select(scenario_id).path.is_file()
    with pytest.raises(ValueError):
        config.select("scenario_unknown")


def test_manta_topology_and_edges(system):
    controller, task, _ = system
    assert controller.pattern == "star"
    assert len(controller.spec.agents) == 3
    assert ("agent_1", "agent_0") in communication_edges(controller.spec)
    assert ("agent_1", "agent_2") not in communication_edges(controller.spec)
    assert controller.spec.agent("agent_1").allowed_tools == ()
    assert controller.spec.agent("agent_1").context.evidence_access == "own"
    controller.update(task.text, (), "commit")
    assert controller.pattern == "chain"
    assert ("agent_0", "agent_2") not in communication_edges(controller.spec)


def test_tool_permissions_and_resources(system):
    controller, task, writes = system
    with pytest.raises(PermissionError, match="tool_permission"):
        controller.tools.call(controller.spec, "agent_1", controller.spec.version,
                              "write", "resource", {"value": "x"}, task.evidence_id)
    with pytest.raises(PermissionError, match="resource_permission"):
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "write", "other", {"value": "x"}, task.evidence_id)
    assert writes == []


def test_low_trust_propagates_to_derived_and_blocks_write(system):
    controller, task, writes = system
    _, observed = controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                                        "read", "resource", {}, task.evidence_id)
    derived = controller.provenance.add("summary", "derived", "looks safe",
                                        (observed.evidence_id,))
    assert controller.provenance.contains_low_trust(derived.evidence_id)
    with pytest.raises(PermissionError, match="low_trust_sensitive_write"):
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "write", "resource", {"value": "x"}, derived.evidence_id)
    assert writes == []


def test_message_gateway_and_reconfiguration(system):
    controller, task, _ = system
    worker_evidence = controller.provenance.add(
        "worker", "derived", "hello", (task.evidence_id,), owner_agent="agent_1")
    with pytest.raises(PermissionError, match="edge absent"):
        controller.messages.send(controller.spec, Message(
            "agent_1", "agent_2", controller.spec.version, worker_evidence.evidence_id, "hello"))
    old = controller.spec.version
    controller.messages.send(controller.spec, Message(
        "agent_1", "agent_0", old, worker_evidence.evidence_id, "hello"))
    controller.safe_reconfigure(task.text, ())
    assert controller.messages.deliver(controller.spec) == []
    with pytest.raises(PermissionError, match="quarantined"):
        controller.messages.send(controller.spec, Message(
            "agent_1", "agent_0", controller.spec.version, worker_evidence.evidence_id, "hello"))
    assert ("agent_2", "agent_0") in communication_edges(controller.spec)
    with pytest.raises(PermissionError, match="stale_version"):
        controller.tools.call(controller.spec, "agent_0", old, "read", "resource",
                              {}, task.evidence_id)


def test_message_cannot_launder_task_provenance(system):
    controller, task, writes = system
    with pytest.raises(PermissionError, match="coordinator"):
        controller.messages.send(controller.spec, Message(
            "agent_1", "agent_0", controller.spec.version, task.evidence_id, task.text))
    worker_evidence = controller.provenance.add(
        "worker", "derived", "unverified claim", (task.evidence_id,), owner_agent="agent_1")
    with pytest.raises(PermissionError, match="does not match"):
        controller.messages.send(controller.spec, Message(
            "agent_1", "agent_0", controller.spec.version,
            worker_evidence.evidence_id, "forged text"))
    with pytest.raises(PermissionError, match="write_requires_task_authorization"):
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "write", "resource", {"value": "x"}, worker_evidence.evidence_id)
    assert writes == []


def test_write_is_at_most_once_across_reconfiguration(system):
    controller, task, writes = system
    controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                          "write", "resource", {"value": "x"}, task.evidence_id)
    controller.update(task.text, (), "commit")
    with pytest.raises(PermissionError, match="duplicate_write"):
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "write", "resource", {"value": "x"}, task.evidence_id)
    assert writes == ["x"]


def test_offline_end_to_end():
    scenario = load_config().scenario_ids[0]
    path = ROOT / "work" / "test_logs" / f"{uuid4().hex}.jsonl"
    result = run_demo(scenario, path)
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert result["mode"] == "offline_mock"
    assert result["topology_sequence"][:3] == ["singleton", "star", "chain"]
    assert [event["kind"] for event in events].count("risk_event") == 1
    assert [event["kind"] for event in events].count("organization_changed") == 4
    assert result["messages_delivered"] == 2
    assert result["mock_calls"].count(("write", "approved")) == 1
