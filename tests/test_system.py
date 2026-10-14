import json
import hashlib
from uuid import uuid4

import pytest

from security_adaptive_mas.config import ROOT, load_config
from security_adaptive_mas import dataset_store
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.demo import run_demo
from security_adaptive_mas.organization import communication_edges
from security_adaptive_mas.security import Message, TaskAuthorization, ToolDefinition
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
    authorized_actions = [
        {"tool": "write", "operation": "write", "resource": "resource",
         "arguments": args, "recipient_field": recipient_field,
         "recipient": recipient, "phase": phase}
        for args, recipient_field, recipient, phase in [
            ({"value": "x"}, None, None, "parallel"),
            ({"value": "x"}, None, None, "commit"),
            ({"value": "once"}, None, None, "parallel"),
            ({"value": "to A"}, "value", "to A", "parallel"),
        ]
    ]
    task_text = json.dumps({"task": "do task", "authorized_actions": authorized_actions})
    task = controller.provenance.add("user", "trusted_task", task_text)
    controller.update(task.text, ("a", "b"), "parallel")
    return controller, task, writes


def grant_write(controller, task, arguments, recipient_field=None, recipient=None):
    controller.authorization.grant(TaskAuthorization(
        "agent_0", controller.spec.version, "write", "write", "resource",
        recipient_field, recipient, arguments, controller.authorization.phase,
        task.evidence_id))


def test_fixed_config_and_hashes():
    config = load_config()
    assert len(config.scenario_ids) == 5
    for scenario_id in config.scenario_ids:
        assert config.select(scenario_id).path.is_file()
    with pytest.raises(ValueError):
        config.select("scenario_unknown")


def test_full_gaia2_category_selection(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    source = tmp_path / "execution.parquet"
    raw = '{"scenario_id":"scenario_demo"}'
    pq.write_table(pa.table({"scenario_id": ["scenario_other", "scenario_demo"],
                             "data": ["{}", raw]}), source)
    monkeypatch.setattr(dataset_store, "source_versions", lambda: {"files": {
        source.name: {"bytes": source.stat().st_size,
                      "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}})
    assert dataset_store.list_scenarios("execution", tmp_path) == ["scenario_other", "scenario_demo"]
    path, digest, index = dataset_store.export_scenario("execution", "scenario_demo",
                                                         tmp_path, tmp_path / "selected")
    assert path.read_bytes() == raw.encode("utf-8")
    assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert index == 1


def test_manta_topology_and_edges(system):
    controller, task, _ = system
    assert controller.pattern == "star"
    assert len(controller.spec.agents) == 3
    assert ("agent_1", "agent_0") in communication_edges(controller.spec)
    assert ("agent_1", "agent_2") not in communication_edges(controller.spec)
    assert controller.spec.agent("agent_1").allowed_tools == ("read",)
    assert controller.spec.agent("agent_1").context.evidence_access == "own"
    controller.update(task.text, (), "commit")
    assert controller.pattern == "chain"
    assert ("agent_0", "agent_1") in communication_edges(controller.spec)


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
    grant_write(controller, task, {"value": "x"})
    controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                          "write", "resource", {"value": "x"}, task.evidence_id)
    controller.update(task.text, (), "commit")
    grant_write(controller, task, {"value": "x"})
    with pytest.raises(PermissionError, match="duplicate_write"):
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "write", "resource", {"value": "x"}, task.evidence_id)
    assert writes == ["x"]


def test_trusted_task_does_not_authorize_other_action(system):
    controller, task, writes = system
    grant_write(controller, task, {"value": "to A"}, "value", "to A")
    with pytest.raises(PermissionError, match="action_not_authorized"):
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "write", "resource", {"value": "to B"}, task.evidence_id)
    with pytest.raises(PermissionError, match="resource_permission"):
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "write", "other", {"value": "to A"}, task.evidence_id)
    with pytest.raises(PermissionError, match="action_not_authorized"):
        controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                              "write", "resource", {"value": "to A", "extra": True}, task.evidence_id)
    assert writes == []


def test_freeform_trusted_text_cannot_create_write_grant(system):
    controller, _, _ = system
    plain = controller.provenance.add("user", "trusted_task", "Send to A")
    with pytest.raises(ValueError, match="structured action"):
        grant_write(controller, plain, {"value": "to B"})


def test_quarantine_blocks_inflight_receive_and_shared_memory(system):
    controller, task, _ = system
    evidence = controller.provenance.add("worker", "derived", "evidence",
                                         (task.evidence_id,), owner_agent="agent_1")
    queued = Message("agent_1", "agent_0", controller.spec.version,
                     evidence.evidence_id, evidence.text)
    controller.messages.send(controller.spec, queued)
    controller.quarantine("agent_1", "send")
    assert controller.messages.deliver(controller.spec) == []
    with pytest.raises(PermissionError, match="Sender quarantined"):
        controller.messages.publish_shared(controller.spec, queued)
    controller.messages.send_blocked.clear()
    controller.messages.publish_shared(controller.spec, queued)
    assert controller.messages.read_shared(controller.spec, "agent_0") == [queued]
    controller.quarantine("agent_0", "receive")
    assert controller.messages.read_shared(controller.spec, "agent_0") == []
    with pytest.raises(PermissionError, match="Recipient quarantined"):
        controller.messages.send(controller.spec, queued)


def test_verifier_receives_authorized_evidence_after_reconfiguration(system):
    controller, task, _ = system
    controller.safe_reconfigure(task.text, ())
    verifier = controller.spec.agent("agent_2")
    assert verifier.structural_role == "verifier"
    message = Message("agent_0", "agent_2", controller.spec.version,
                      task.evidence_id, task.text)
    controller.messages.send(controller.spec, message)
    assert controller.messages.deliver(controller.spec) == [message]
    assert controller.spec.agent("agent_1").allowed_tools == ("read",)
    with pytest.raises(PermissionError, match="agent_quarantined"):
        controller.tools.call(controller.spec, "agent_1", controller.spec.version,
                              "write", "resource", {"value": "x"}, task.evidence_id)


def test_worker_read_permission_is_role_scoped(system):
    controller, task, writes = system
    result, _ = controller.tools.call(controller.spec, "agent_1", controller.spec.version,
                                      "read", "resource", {}, task.evidence_id)
    assert result == "external content"
    with pytest.raises(PermissionError, match="tool_permission"):
        controller.tools.call(controller.spec, "agent_1", controller.spec.version,
                              "write", "resource", {"value": "x"}, task.evidence_id)
    assert writes == []


def test_old_or_forged_organization_cannot_grant_agent_tools(system):
    from dataclasses import replace
    controller, task, _ = system
    forged_agents = tuple(replace(node, allowed_tools=("read", "write"))
                          if node.agent_id == "agent_1" else node
                          for node in controller.spec.agents)
    forged = replace(controller.spec, agents=forged_agents)
    with pytest.raises(PermissionError, match="stale_or_forged_organization"):
        controller.tools.call(forged, "agent_1", forged.version,
                              "write", "resource", {"value": "x"}, task.evidence_id)
    with pytest.raises(PermissionError, match="Stale or forged organization"):
        controller.messages.send(forged, Message("agent_1", "agent_0", forged.version,
                                                 task.evidence_id, task.text))


def test_write_journal_survives_controller_restart(system):
    controller, task, writes = system
    grant_write(controller, task, {"value": "once"})
    controller.tools.call(controller.spec, "agent_0", controller.spec.version,
                          "write", "resource", {"value": "once"}, task.evidence_id)
    restarted = Controller(load_config(), TraceLogger(controller.trace.path),
                           frozenset({"write"}), frozenset({"resource"}))
    restarted.tools.register(ToolDefinition("write", "write", frozenset({"resource"}),
                                             lambda value: writes.append(value)))
    new_task = restarted.provenance.add("user", "trusted_task", task.text)
    restarted.update(new_task.text, ("a", "b"), "parallel")
    grant_write(restarted, new_task, {"value": "once"})
    with pytest.raises(PermissionError, match="duplicate_write"):
        restarted.tools.call(restarted.spec, "agent_0", restarted.spec.version,
                             "write", "resource", {"value": "once"}, new_task.evidence_id)
    assert writes == ["once"]


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
