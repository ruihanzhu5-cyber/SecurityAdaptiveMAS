from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .organization import communication_edges


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source: str
    trust: str  # trusted_task | low_trust | derived
    parent_ids: tuple[str, ...]
    text: str
    owner_agent: str | None


class ProvenanceStore:
    def __init__(self, trace):
        self.trace = trace
        self.items: dict[str, Evidence] = {}

    def add(self, source: str, trust: str, text: str,
            parents: tuple[str, ...] = (), owner_agent: str | None = None) -> Evidence:
        if trust not in {"trusted_task", "low_trust", "derived"}:
            raise ValueError("Unknown trust label")
        if trust == "derived" and not parents:
            raise ValueError("Derived evidence needs parents")
        if trust != "derived" and parents:
            raise ValueError("Only derived evidence may have parents")
        for parent in parents:
            if parent not in self.items:
                raise ValueError(f"Unknown parent evidence {parent}")
        evidence_id = f"ev_{len(self.items) + 1}"
        if trust == "trusted_task" and owner_agent is not None:
            raise ValueError("Only the controller may register trusted task evidence")
        evidence = Evidence(evidence_id, source, trust, parents, text, owner_agent)
        self.items[evidence_id] = evidence
        self.trace.log("evidence", evidence_id=evidence_id, source=source,
                       trust=trust, parent_ids=parents, text=text)
        return evidence

    def contains_low_trust(self, evidence_id: str) -> bool:
        item = self.items[evidence_id]
        return item.trust == "low_trust" or any(
            self.contains_low_trust(parent) for parent in item.parent_ids
        )


@dataclass(frozen=True)
class Message:
    source_agent: str
    target_agent: str
    version: int
    evidence_id: str
    text: str


class MessageGateway:
    def __init__(self, provenance: ProvenanceStore, trace):
        self.provenance, self.trace = provenance, trace
        self.pending: list[Message] = []
        self.blocked_agents: set[str] = set()

    def send(self, spec, message: Message) -> None:
        if message.version != spec.version:
            raise PermissionError("Stale organization version")
        if message.source_agent in self.blocked_agents:
            raise PermissionError("Agent quarantined")
        if (message.source_agent, message.target_agent) not in communication_edges(spec):
            raise PermissionError("Communication edge absent")
        target = spec.agent(message.target_agent)
        if message.source_agent not in target.context.visible_from:
            raise PermissionError("Context visibility denies sender")
        if message.evidence_id not in self.provenance.items:
            raise PermissionError("Unregistered evidence")
        evidence = self.provenance.items[message.evidence_id]
        if evidence.owner_agent not in {message.source_agent, None}:
            raise PermissionError("Evidence belongs to another agent")
        if evidence.owner_agent is None and message.source_agent != "agent_0":
            raise PermissionError("Only coordinator can forward controller evidence")
        if message.text != evidence.text:
            raise PermissionError("Message text does not match recorded evidence")
        if len(message.text) > target.context.max_packet_chars:
            raise PermissionError("Context packet too large")
        self.pending.append(message)
        self.trace.log("message_queued", source=message.source_agent,
                       target=message.target_agent, version=message.version,
                       evidence_id=message.evidence_id)

    def deliver(self, spec) -> list[Message]:
        ready, self.pending = self.pending, []
        delivered = []
        for message in ready:
            if message.version != spec.version:
                self.trace.log("message_dropped", reason="stale_version",
                               evidence_id=message.evidence_id)
                continue
            if message.source_agent in self.blocked_agents:
                self.trace.log("message_dropped", reason="agent_quarantined",
                               evidence_id=message.evidence_id)
                continue
            try:
                if (message.source_agent, message.target_agent) not in communication_edges(spec):
                    raise PermissionError("edge changed")
                if message.source_agent not in spec.agent(message.target_agent).context.visible_from:
                    raise PermissionError("visibility changed")
            except (PermissionError, KeyError) as error:
                self.trace.log("message_dropped", reason=str(error),
                               evidence_id=message.evidence_id)
                continue
            delivered.append(message)
            self.trace.log("message_delivered", source=message.source_agent,
                           target=message.target_agent, evidence_id=message.evidence_id)
        return delivered


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    mode: str  # read | write
    resources: frozenset[str]
    function: Callable[..., Any]


class ToolGateway:
    def __init__(self, provenance: ProvenanceStore, trace,
                 authorized_tools: frozenset[str], authorized_resources: frozenset[str]):
        self.provenance, self.trace = provenance, trace
        self.authorized_tools = authorized_tools
        self.authorized_resources = authorized_resources
        self.tools: dict[str, ToolDefinition] = {}
        self.completed_writes: dict[str, Any] = {}
        self.pending_writes: set[str] = set()
        self.lock = threading.Lock()
        self.blocked_agents: set[str] = set()

    def register(self, definition: ToolDefinition):
        if definition.mode not in {"read", "write"}:
            raise ValueError("Tool mode must be read or write")
        self.tools[definition.name] = definition

    def call(self, spec, agent_id: str, version: int, tool_name: str,
             resource: str, arguments: dict[str, Any], evidence_id: str):
        reason = None
        try:
            node = spec.agent(agent_id)
            definition = self.tools[tool_name]
            if version != spec.version:
                reason = "stale_version"
            elif agent_id in self.blocked_agents:
                reason = "agent_quarantined"
            elif (node.allowed_tools is None or tool_name not in node.allowed_tools or
                  tool_name not in self.authorized_tools):
                reason = "tool_permission"
            elif resource not in definition.resources or resource not in self.authorized_resources:
                reason = "resource_permission"
            elif evidence_id not in self.provenance.items:
                reason = "missing_provenance"
            elif not isinstance(arguments, dict):
                reason = "invalid_arguments"
            elif definition.mode == "write" and self.provenance.contains_low_trust(evidence_id):
                reason = "low_trust_sensitive_write"
            elif definition.mode == "write" and self.provenance.items[evidence_id].trust != "trusted_task":
                reason = "write_requires_task_authorization"
        except (KeyError, TypeError):
            reason = "unknown_agent_or_tool"
        if reason:
            self.trace.log("tool_denied", agent_id=agent_id, tool=tool_name,
                           resource=resource, version=version, reason=reason,
                           evidence_id=evidence_id)
            if reason == "low_trust_sensitive_write":
                self.trace.log("risk_event", type="untrusted_input_to_write",
                               agent_id=agent_id, evidence_id=evidence_id,
                               tool=tool_name)
            raise PermissionError(reason)
        definition = self.tools[tool_name]
        fingerprint = hashlib.sha256(json.dumps(
            [tool_name, resource, arguments], sort_keys=True, ensure_ascii=False,
            default=str).encode("utf-8")).hexdigest()
        if definition.mode == "write":
            with self.lock:
                if fingerprint in self.completed_writes or fingerprint in self.pending_writes:
                    self.trace.log("tool_denied", agent_id=agent_id, tool=tool_name,
                                   resource=resource, reason="duplicate_write")
                    raise PermissionError("duplicate_write")
                self.pending_writes.add(fingerprint)
        self.trace.log("tool_started", agent_id=agent_id, tool=tool_name,
                       resource=resource, version=version, arguments=arguments,
                       evidence_id=evidence_id, fingerprint=fingerprint)
        try:
            result = definition.function(**arguments)
        except Exception as error:
            # A failed remote call may have committed. Preserve fingerprint and
            # require reconciliation instead of retrying an ambiguous write.
            self.trace.log("tool_error", agent_id=agent_id, tool=tool_name,
                           fingerprint=fingerprint, error=str(error))
            raise
        if definition.mode == "write":
            with self.lock:
                self.pending_writes.remove(fingerprint)
                self.completed_writes[fingerprint] = result
        observed = self.provenance.add(
            source=f"tool:{tool_name}", trust="low_trust", text=str(result),
            owner_agent=agent_id)
        self.trace.log("tool_completed", agent_id=agent_id, tool=tool_name,
                       fingerprint=fingerprint, output_evidence_id=observed.evidence_id)
        return result, observed
