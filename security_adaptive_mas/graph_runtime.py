"""One effective graph for serial v0.1 dispatch, delivery and tool identity."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, asdict
from types import SimpleNamespace


@dataclass
class RuntimeAgent:
    agent_id: str
    allowed_tools: frozenset[str]
    context: object = field(default_factory=lambda: SimpleNamespace(
        visible_from=frozenset(), max_packet_chars=16000))
    role_contract: object | None = None
    role_revision: int = 0
    lifecycle_status: str = "active"


@dataclass
class RuntimeGraph:
    version: int
    agents: tuple[RuntimeAgent, ...]
    extra_edges: set[tuple[str, str]] = field(default_factory=set)
    object_access: set[tuple[str, str]] = field(default_factory=set)
    assignments: dict[str, str] = field(default_factory=dict)
    root_group_id: str = "runtime"

    def agent(self, agent_id):
        return next((agent for agent in self.agents if agent.agent_id == agent_id), None) or self._missing(agent_id)

    @staticmethod
    def _missing(agent_id):
        raise KeyError(agent_id)

    def group(self, group_id):
        if group_id != self.root_group_id:
            raise KeyError(group_id)
        return SimpleNamespace(pattern="explicit", member_ids=tuple(a.agent_id for a in self.agents))


@dataclass(frozen=True)
class TopologyPatch:
    patch_id: str
    base_graph_version: int
    base_observation_sequence: int
    base_task_revision: int
    operations: tuple[dict, ...]
    assignments: tuple[tuple[str, str], ...] = ()
    delivery_plans: tuple[tuple[str, str], ...] = ()
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()


class GraphRuntime:
    def __init__(self, controller, coordinator_tools, worker_tools, max_agents=3):
        self.controller = controller
        self.worker_tools = frozenset(worker_tools)
        self.max_agents = max_agents
        self.objects = {}
        self.instances = {"agent_0"}
        self.max_active = 1
        self.applied = 0
        self.flow_events = []
        self.exposures = {}
        self.receipts = set()
        self.checks = {}
        self.bindings = {}
        self.submitted_component_versions = set()
        self.install(RuntimeGraph(1, (RuntimeAgent("agent_0", frozenset(coordinator_tools)),)))

    @property
    def graph(self):
        return self.controller.spec

    def install(self, graph):
        for agent in graph.agents:
            agent.context.visible_from = frozenset(src for src, dst in graph.extra_edges if dst == agent.agent_id)
        self.controller.spec = graph
        self.controller.tools.current_spec = graph
        self.controller.messages.current_spec = graph
        self.controller.assignments = graph.assignments
        self.controller.pattern = "runtime"
        self.controller.runtime_agent_instances = tuple(a.agent_id for a in graph.agents)

    def preview(self, patch):
        observation = self.controller.online_snapshot()
        if (patch.base_graph_version != self.graph.version or
                patch.base_observation_sequence != observation.sequence or
                patch.base_task_revision != self.controller.task_revision):
            raise ValueError("stale_topology_patch")
        if not set(patch.evidence_ids) <= set(observation.evidence_ids):
            raise ValueError("patch_unseen_evidence")
        graph = deepcopy(self.graph)
        agents = {a.agent_id: a for a in graph.agents}
        for op in patch.operations:
            kind = op["op"]
            if kind == "KEEP":
                continue
            if kind == "SPAWN_WORKER":
                agent_id = op["agent_id"]
                tools = frozenset(op["tools"])
                if agent_id in self.instances or agent_id in agents or not tools <= self.worker_tools:
                    raise ValueError("invalid_worker_or_capability")
                agents[agent_id] = RuntimeAgent(agent_id, tools)
            elif kind == "SET_ROLE":
                agent = agents[op["agent_id"]]
                role = op["role"]
                if not set(role.requested_tools) <= agent.allowed_tools:
                    raise ValueError("role_exceeds_granted_capabilities")
                if not set(role.source_evidence_ids) <= set(observation.evidence_ids):
                    raise ValueError("role_cites_unseen_runtime_evidence")
                agent.role_contract = role
                agent.allowed_tools = frozenset(role.requested_tools)
                agent.role_revision += 1
                agent.lifecycle_status = "active"
            elif kind in {"SUSPEND_WORKER", "RESUME_WORKER"}:
                agents[op["agent_id"]].lifecycle_status = "suspended" if kind == "SUSPEND_WORKER" else "active"
            elif kind in {"ADD_ROUTE", "REMOVE_ROUTE"}:
                edge = (op["source"], op["target"])
                if not set(edge) <= agents.keys():
                    raise ValueError("route_unknown_agent")
                if kind == "ADD_ROUTE":
                    graph.extra_edges.add(edge)
                else:
                    graph.extra_edges.discard(edge)
            elif kind == "ASSIGN":
                if op["agent_id"] not in agents:
                    raise ValueError("assignment_unknown_agent")
                graph.assignments[op["task_id"]] = op["agent_id"]
            elif kind == "SET_OBJECT_ACCESS":
                key = (op["agent_id"], op["object_id"])
                if key[0] not in agents or key[1] not in self.objects:
                    raise ValueError("access_unknown_agent_or_object")
                if op["allowed"]:
                    graph.object_access.add(key)
                else:
                    graph.object_access.discard(key)
            elif kind == "RETIRE_WORKER":
                agent_id = op["agent_id"]
                if agent_id == "agent_0" or agent_id not in agents:
                    raise ValueError("invalid_retirement")
                del agents[agent_id]
                graph.extra_edges = {edge for edge in graph.extra_edges if agent_id not in edge}
                graph.object_access = {key for key in graph.object_access if key[0] != agent_id}
                graph.assignments = {task: owner for task, owner in graph.assignments.items() if owner != agent_id}
            else:
                raise ValueError(f"unsupported_graph_operation:{kind}")
        if not 1 <= len(agents) <= self.max_agents:
            raise ValueError("active_agent_limit")
        for task_id, agent_id in patch.assignments:
            if agent_id not in agents:
                raise ValueError("assignment_unknown_agent")
            graph.assignments[task_id] = agent_id
        for agent_id, object_id in patch.delivery_plans:
            if (agent_id, object_id) not in graph.object_access:
                raise ValueError("delivery_without_object_access")
        graph.agents = tuple(agents.values())
        if patch.operations != ({"op": "KEEP"},):
            graph.version += 1
        return graph

    def apply(self, patch):
        graph = self.preview(patch)
        previous = self.graph.version
        design = self.controller.design
        if design is not None and graph.version != previous and self.applied >= design["reconfiguration_limit"]:
            raise RuntimeError("configured_effective_graph_patch_limit_exhausted")
        self.install(graph)
        self.instances.update(a.agent_id for a in graph.agents)
        self.max_active = max(self.max_active, len(graph.agents))
        self.applied += int(graph.version != previous)
        self.controller.trace.log("topology_patch_applied", patch_id=patch.patch_id,
                                  previous_version=previous, graph_version=graph.version,
                                  operations=[{**op,"role":asdict(op["role"])} if op["op"] == "SET_ROLE" else op
                                              for op in patch.operations], assignments=graph.assignments,
                                  delivery_plans=patch.delivery_plans,
                                  observation_sequence=patch.base_observation_sequence,
                                  evidence_ids=patch.evidence_ids)

    def add_object(self, evidence_id, writer, *, task_id=None, status="output_available",
                   activation_id=None, fields=None, task_description=None):
        evidence = self.controller.provenance.items[evidence_id]
        if evidence_id in self.objects:
            raise ValueError("immutable_object_already_registered")
        self.objects[evidence_id] = {"object_id": evidence_id, "version": 1,
            "writer": writer, "task_id": task_id, "status": status,
            "parent_ids": evidence.parent_ids, "low_trust": self.controller.provenance.contains_low_trust(evidence_id),
            "source_trust": evidence.trust, "producer_activation": activation_id,
            "producer_task_description":task_description,
            "fields": fields, "created_sequence": len(self.flow_events)+1}
        self.graph.object_access.add((writer, evidence_id))
        self.controller.trace.log("runtime_object_created", **self.objects[evidence_id])
        self.record_flow("object_created", instance_id=writer, object_id=evidence_id,
                         activation_id=activation_id, parent_ids=evidence.parent_ids)

    def read_object(self, agent_id, object_id):
        self.graph.agent(agent_id)
        if (agent_id, object_id) not in self.graph.object_access:
            raise PermissionError("object_access_denied")
        evidence = self.controller.provenance.items[object_id]
        self.controller.trace.log("runtime_object_delivered", agent_id=agent_id,
                                  object_id=object_id, graph_version=self.graph.version)
        return evidence.text

    def record_flow(self, kind, **data):
        row = {"flow_sequence": len(self.flow_events)+1, "flow_kind": kind, **data}
        self.flow_events.append(row)
        self.controller.trace.log("information_flow", **row)
        return row

    def deliver_object(self, consumer, object_id):
        """Persistent object store can deliver retired authors' artifacts under ACL."""
        self.graph.agent(consumer)
        if (consumer, object_id) not in self.graph.object_access:
            raise PermissionError("object_access_denied")
        obj = self.objects[object_id]
        writer = obj["writer"]
        active = {a.agent_id for a in self.graph.agents}
        transport = "live_route" if writer in active else "persistent_store"
        trusted_goal = self.controller.provenance.items[object_id].trust == "trusted_task"
        if trusted_goal:
            transport = "trusted_goal_control"
        if not trusted_goal and writer in active and writer != consumer and (writer,consumer) not in self.graph.extra_edges:
            raise PermissionError("object_delivery_route_denied")
        receipt = (consumer, object_id, obj["version"])
        if receipt in self.receipts:
            return None
        self.receipts.add(receipt)
        self.record_flow("object_delivered", instance_id=consumer, object_id=object_id,
                         author_instance_id=writer, transport=transport)
        self.controller.trace.log("runtime_object_delivered", agent_id=consumer, object_id=object_id,
                                  object_version=obj["version"], author_instance_id=writer,
                                  transport=transport, graph_version=self.graph.version)
        return self.controller.provenance.items[object_id].text

    def record_submission(self, instance_id, activation_id, object_ids, request_id):
        self.exposures.setdefault(instance_id, set()).update(object_ids)
        repeated = 0
        for key in object_ids:
            version = self.objects[key]["version"]
            identity = (instance_id,key,version)
            if identity in self.submitted_component_versions:
                repeated += len(self.controller.provenance.items[key].text)
            self.submitted_component_versions.add(identity)
        self.controller.observed_repeated_object_chars += repeated
        self.record_flow("model_submitted", instance_id=instance_id, activation_id=activation_id,
                         object_ids=tuple(object_ids), request_id=request_id,
                         repeated_object_characters=repeated)

    def bind_output(self, task_id, slot, object_id, check):
        if object_id not in self.objects or self.objects[object_id]["task_id"] != task_id:
            raise ValueError("output_binding_requires_actual_producer")
        self.bindings[(task_id, slot)] = object_id
        self.checks[object_id] = check
        self.record_flow("output_checked", object_id=object_id, task_id=task_id,
                         output_slot=slot, check=check)
