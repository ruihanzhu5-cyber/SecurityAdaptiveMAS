from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
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

    def derived_from(self, evidence_id: str, parent_id: str) -> bool:
        item = self.items[evidence_id]
        return evidence_id == parent_id or any(
            self.derived_from(parent, parent_id) for parent in item.parent_ids
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
        self.send_blocked: set[str] = set()
        self.receive_blocked: set[str] = set()
        self.shared: list[Message] = []
        self.current_spec = None

    def quarantine(self, agent_id: str, mode: str = "full") -> None:
        if mode == "full":
            self.blocked_agents.add(agent_id)
        elif mode == "send":
            self.send_blocked.add(agent_id)
        elif mode == "receive":
            self.receive_blocked.add(agent_id)
        else:
            raise ValueError("Unknown quarantine mode")
        self.trace.log("agent_quarantined", agent_id=agent_id, quarantine_mode=mode)

    def _check_route(self, spec, message: Message) -> None:
        if spec is not self.current_spec:
            raise PermissionError("Stale or forged organization")
        if message.version != spec.version:
            raise PermissionError("Stale organization version")
        if message.source_agent in self.blocked_agents or message.source_agent in self.send_blocked:
            raise PermissionError("Sender quarantined")
        if message.target_agent in self.blocked_agents or message.target_agent in self.receive_blocked:
            raise PermissionError("Recipient quarantined")
        if (message.source_agent, message.target_agent) not in communication_edges(spec):
            raise PermissionError("Communication edge absent")
        target = spec.agent(message.target_agent)
        if message.source_agent not in target.context.visible_from:
            raise PermissionError("Context visibility denies sender")

    def send(self, spec, message: Message) -> None:
        self._check_route(spec, message)
        target = spec.agent(message.target_agent)
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
            try:
                self._check_route(spec, message)
            except (PermissionError, KeyError) as error:
                self.trace.log("message_dropped", reason=str(error),
                               evidence_id=message.evidence_id)
                continue
            delivered.append(message)
            self.trace.log("message_delivered", source=message.source_agent,
                           target=message.target_agent, evidence_id=message.evidence_id)
        return delivered

    def publish_shared(self, spec, message: Message) -> None:
        self.send(spec, message)
        self.pending.pop()
        self.shared.append(message)
        self.trace.log("shared_memory_published", evidence_id=message.evidence_id,
                       source=message.source_agent, target=message.target_agent)

    def read_shared(self, spec, agent_id: str) -> list[Message]:
        visible = []
        for message in self.shared:
            if message.target_agent != agent_id:
                continue
            try:
                self._check_route(spec, message)
            except (PermissionError, KeyError):
                continue
            visible.append(message)
        return visible


@dataclass(frozen=True)
class TaskAuthorization:
    agent_id: str
    version: int
    tool_name: str
    operation: str
    resource: str
    recipient_field: str | None
    recipient: str | None
    arguments: dict[str, Any]
    phase: str
    evidence_id: str


class TaskAuthorizationPolicy:
    """Controller-issued, exact-action grants; source trust alone is insufficient."""

    def __init__(self, provenance: ProvenanceStore, trace):
        self.provenance, self.trace = provenance, trace
        self.grants: list[TaskAuthorization] = []
        self.phase = "intake"

    def grant(self, authorization: TaskAuthorization) -> None:
        evidence = self.provenance.items[authorization.evidence_id]
        if evidence.trust != "trusted_task" or evidence.owner_agent is not None:
            raise ValueError("Grant requires controller-owned trusted task evidence")
        if authorization.operation != "write":
            raise ValueError("Only sensitive writes require action grants")
        try:
            task = json.loads(evidence.text)
            listed = task["authorized_actions"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError("Trusted task lacks explicit structured action authorization") from error
        expected_action = {"tool": authorization.tool_name,
                           "operation": authorization.operation,
                           "resource": authorization.resource,
                           "arguments": authorization.arguments,
                           "recipient_field": authorization.recipient_field,
                           "recipient": authorization.recipient,
                           "phase": authorization.phase}
        if not isinstance(listed, list) or expected_action not in listed:
            raise ValueError("Requested action is not listed in trusted task evidence")
        if authorization.recipient_field is not None and (
            authorization.arguments.get(authorization.recipient_field) != authorization.recipient
        ):
            raise ValueError("Recipient does not match granted arguments")
        self.grants.append(authorization)
        self.trace.log("task_authorization_granted", agent_id=authorization.agent_id,
                       version=authorization.version, tool=authorization.tool_name,
                       operation=authorization.operation, resource=authorization.resource,
                       recipient=authorization.recipient, phase=authorization.phase,
                       evidence_id=authorization.evidence_id)

    def permits(self, agent_id: str, version: int, tool_name: str, operation: str,
                resource: str, arguments: dict[str, Any], evidence_id: str) -> bool:
        return any(
            grant.agent_id == agent_id and grant.version == version and
            grant.tool_name == tool_name and grant.operation == operation and
            grant.resource == resource and grant.phase == self.phase and
            grant.evidence_id == evidence_id and grant.arguments == arguments and
            (grant.recipient_field is None or
             arguments.get(grant.recipient_field) == grant.recipient)
            for grant in self.grants
        )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    mode: str  # read | write
    resources: frozenset[str]
    function: Callable[..., Any]


class UnsupportedTaskScope(PermissionError):
    """A requested write has no implemented trusted authorization contract."""


class TaskScopePolicy:
    """Opt-in v0.1 policy. External data may fill data fields, never controls.

    Structured scopes must arrive in an actual USER notification. Natural
    language support is deliberately limited to an explicit absolute mkdir
    command. Conditional calendar/contact/shopping authorization is OPEN.
    """
    allow_data_dependencies = True
    version = "v0.1-explicit-scopes-1"

    def __init__(self, provenance, trace):
        self.provenance, self.trace = provenance, trace
        self.user_ids = []
        self.scopes = []

    def register_user(self, evidence_id):
        import re
        from pathlib import PurePosixPath
        evidence = self.provenance.items[evidence_id]
        if evidence.trust != "trusted_task" or evidence.owner_agent is not None:
            raise ValueError("scope_requires_delivered_user")
        self.user_ids.append(evidence_id)
        if evidence.text.lstrip().startswith('{'):
            data = json.loads(evidence.text)
            for scope in data.get("authorized_scopes", []):
                if set(scope) != {"tool", "resource", "control_fields", "data_fields"}:
                    raise ValueError("invalid_trusted_scope")
                if not isinstance(scope["control_fields"], dict) or not isinstance(scope["data_fields"], list):
                    raise ValueError("invalid_trusted_scope_fields")
                if set(scope["control_fields"]) & set(scope["data_fields"]):
                    raise ValueError("control_data_overlap")
                self.scopes.append((evidence_id, scope))
        for match in re.finditer(r'(?i)\b(?:mkdir|create (?:a )?(?:directory|folder))\s+(/[^\s"\x27,;]+)', evidence.text):
            path = match.group(1).rstrip('.')
            if '..' in PurePosixPath(path).parts or path == '/':
                raise ValueError("invalid_user_output_path")
            self.scopes.append((evidence_id, {"tool": "Files__mkdir", "resource": "Files",
                "control_fields": {"path": path}, "data_fields": []}))
        self.trace.log("trusted_scope_registered", evidence_id=evidence_id,
                       scope_count=len(self.scopes), policy_version=self.version)

    def permits(self, agent_id, version, tool_name, operation, resource, arguments, evidence_id):
        if agent_id != "agent_0" or not self.user_ids:
            return False
        # A reply to the trusted user is a fixed system capability, never an
        # authorization to email/message arbitrary external recipients.
        if tool_name == "AgentUserInterface__send_message_to_user":
            return set(arguments) <= {"content"} and isinstance(arguments.get("content", ""), str)
        for source_id, scope in self.scopes:
            if scope["tool"] != tool_name or scope["resource"] != resource:
                continue
            controls = scope["control_fields"]
            if (set(arguments) <= set(controls) | set(scope["data_fields"]) and
                    all(arguments.get(key) == value for key, value in controls.items())):
                self.trace.log("task_scope_permitted", agent_id=agent_id, tool=tool_name,
                               control_evidence_id=source_id, data_evidence_id=evidence_id)
                return True
        return False


class ToolGateway:
    def __init__(self, provenance: ProvenanceStore, trace,
                 authorized_tools: frozenset[str], authorized_resources: frozenset[str],
                 authorization: TaskAuthorizationPolicy | None = None):
        self.provenance, self.trace = provenance, trace
        self.authorized_tools = authorized_tools
        self.authorized_resources = authorized_resources
        self.tools: dict[str, ToolDefinition] = {}
        self.completed_writes: dict[str, Any] = {}
        self.pending_writes: set[str] = set()
        self.lock = threading.Lock()
        self.blocked_agents: set[str] = set()
        self.authorization = authorization or TaskAuthorizationPolicy(provenance, trace)
        self.current_spec = None
        self.journal_path = Path(trace.path).with_suffix(".writes.jsonl")
        if self.journal_path.exists():
            for line in self.journal_path.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                if entry["state"] == "started":
                    self.pending_writes.add(entry["fingerprint"])
                elif entry["state"] == "completed":
                    self.pending_writes.discard(entry["fingerprint"])
                    self.completed_writes[entry["fingerprint"]] = True

    def _journal(self, fingerprint: str, state: str) -> None:
        with self.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"fingerprint": fingerprint, "state": state}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def register(self, definition: ToolDefinition):
        if definition.mode not in {"read", "write"}:
            raise ValueError("Tool mode must be read or write")
        self.tools[definition.name] = definition

    def call(self, spec, agent_id: str, version: int, tool_name: str,
             resource: str, arguments: dict[str, Any], evidence_id: str):
        reason = None
        try:
            if spec is not self.current_spec:
                reason = "stale_or_forged_organization"
            node = spec.agent(agent_id)
            definition = self.tools[tool_name]
            if reason:
                pass
            elif version != spec.version:
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
            elif definition.mode == "write" and not getattr(self.authorization, "allow_data_dependencies", False) and self.provenance.contains_low_trust(evidence_id):
                reason = "low_trust_sensitive_write"
            elif definition.mode == "write" and not getattr(self.authorization, "allow_data_dependencies", False) and self.provenance.items[evidence_id].trust != "trusted_task":
                reason = "write_requires_task_authorization"
            elif definition.mode == "write" and not self.authorization.permits(
                agent_id, version, tool_name, definition.mode, resource, arguments, evidence_id
            ):
                reason = ("unsupported_task_scope" if isinstance(self.authorization, TaskScopePolicy)
                          else "action_not_authorized")
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
            if reason == "unsupported_task_scope":
                raise UnsupportedTaskScope(f"{reason}:{tool_name}")
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
                self._journal(fingerprint, "started")
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
                self._journal(fingerprint, "completed")
                self.pending_writes.remove(fingerprint)
                self.completed_writes[fingerprint] = result
        observed = self.provenance.add(
            source=f"tool:{tool_name}", trust="low_trust", text=str(result),
            owner_agent=agent_id)
        self.trace.log("tool_completed", agent_id=agent_id, tool=tool_name,
                       fingerprint=fingerprint, output_evidence_id=observed.evidence_id)
        return result, observed
