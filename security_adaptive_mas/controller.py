from __future__ import annotations

from dataclasses import dataclass

from .organization import OrganizationSelector, TaskAnalyzer, TopologyPlanner
from .security import MessageGateway, ProvenanceStore, TaskAuthorizationPolicy, ToolGateway


@dataclass(frozen=True)
class OrganizationSnapshot:
    pattern: str
    version: int
    agent_ids: tuple[str, ...]
    assignments: dict[str, str]


class Controller:
    def __init__(self, config, trace, authorized_tools: frozenset[str],
                 authorized_resources: frozenset[str]):
        self.trace = trace
        self.authorized_tools = authorized_tools
        self.analyzer = TaskAnalyzer()
        self.planner = TopologyPlanner(config.manta_root, config.max_agents)
        self.selector = OrganizationSelector(self.planner, config.weights)
        self.provenance = ProvenanceStore(trace)
        self.messages = MessageGateway(self.provenance, trace)
        self.authorization = TaskAuthorizationPolicy(self.provenance, trace)
        self.tools = ToolGateway(self.provenance, trace, authorized_tools,
                                 authorized_resources, self.authorization)
        self.spec = None
        self.assignments: dict[str, str] = {}
        self.pattern = ""
        self.quarantined: set[str] = set()
        self.messages.blocked_agents = self.quarantined
        self.tools.blocked_agents = self.quarantined

    def quarantine(self, agent_id: str, mode: str = "full") -> None:
        if mode == "full":
            self.quarantined.add(agent_id)
        self.messages.quarantine(agent_id, mode)

    def update(self, task_text: str, pending_subtasks: tuple[str, ...], phase: str,
               observed_tool_calls: int = 0, risky_input: bool = False) -> OrganizationSnapshot:
        state = self.analyzer.analyze(task_text, pending_subtasks, phase,
                                      observed_tool_calls, risky_input)
        version = 1 if self.spec is None else self.spec.version + 1
        read_tools = frozenset(name for name, definition in self.tools.tools.items()
                               if definition.mode == "read" and name in self.authorized_tools)
        selected, trials = self.selector.choose(state, version, self.authorized_tools,
                                                read_tools)
        self.trace.log("organization_selection", phase=phase, candidates=trials,
                       selected=selected.pattern, reason=selected.reason)
        old_version = None if self.spec is None else self.spec.version
        # No gateway sees the MANTA intermediate state. Existing pending writes
        # stay reserved; duplicate fingerprints are never reset by reconfiguration.
        self.spec = selected.spec
        self.tools.current_spec = self.spec
        self.messages.current_spec = self.spec
        self.assignments = selected.assignments
        self.pattern = selected.pattern
        self.authorization.phase = phase
        if risky_input:
            for node in self.spec.agents:
                if node.structural_role == "worker":
                    self.quarantine(node.agent_id)
        self.trace.log("organization_changed", old_version=old_version,
                       new_version=self.spec.version, pattern=self.pattern,
                       assignments=self.assignments,
                       quarantined=sorted(self.quarantined),
                       revoked_old_authorizations=old_version is not None)
        return self.snapshot()

    def snapshot(self) -> OrganizationSnapshot:
        if self.spec is None:
            raise RuntimeError("Controller not initialized")
        return OrganizationSnapshot(self.pattern, self.spec.version,
                                    tuple(n.agent_id for n in self.spec.agents),
                                    dict(self.assignments))

    def safe_reconfigure(self, task_text: str, pending_subtasks: tuple[str, ...],
                         observed_tool_calls: int = 0) -> OrganizationSnapshot:
        self.trace.log("security_reconfiguration_requested", reason="low_trust_to_sensitive_write")
        return self.update(task_text, pending_subtasks, "commit",
                           observed_tool_calls, risky_input=True)
