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
    base_object_epoch: int = 0


class GraphRuntime:
    def __init__(self, *, trace, provenance, tool_gateway, message_gateway,
                 coordinator_tools, worker_tools, max_agents, reconfiguration_limit,
                 observation_stamp, max_total_instances=8):
        self.trace, self.provenance = trace, provenance
        self.tools, self.messages = tool_gateway, message_gateway
        self.observation_stamp = observation_stamp
        self.worker_tools = frozenset(worker_tools)
        self.max_agents, self.max_total_instances = max_agents, max_total_instances
        self.reconfiguration_limit = reconfiguration_limit
        self.objects, self.receipts, self.exposures = {}, set(), {}
        self.instances = {"agent_0"}
        self.max_active, self.applied, self.object_epoch = 1, 0, 0
        self.flow_events, self.submitted_component_versions = [], set()
        self.install(RuntimeGraph(1, (RuntimeAgent("agent_0", frozenset(coordinator_tools)),)))

    @property
    def graph(self):
        return self._graph

    def stamp(self):
        from .orchestration.contracts import RuntimeStamp
        sequence, revision = self.observation_stamp()
        return RuntimeStamp(self.graph.version, sequence, revision, self.object_epoch)

    def install(self, graph):
        for agent in graph.agents:
            agent.context.visible_from = frozenset(src for src, dst in graph.extra_edges if dst == agent.agent_id)
            agent.context.max_packet_chars = 2**63-1
        self._graph = graph
        self.tools.current_spec = self.messages.current_spec = graph

    def preview(self, patch):
        stamp = self.stamp()
        if (patch.base_graph_version != stamp.graph_version or
                patch.base_observation_sequence != stamp.observation_sequence or
                patch.base_task_revision != stamp.goal_revision or
                patch.base_object_epoch != stamp.object_epoch):
            raise ValueError("stale_topology_patch")
        if not set(patch.evidence_ids) <= self.provenance.items.keys():
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
                if not set(role.source_evidence_ids) <= self.provenance.items.keys():
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
                if agents[agent_id].lifecycle_status != "idle":
                    raise ValueError("retirement_requires_idle_worker")
                del agents[agent_id]
                graph.extra_edges = {edge for edge in graph.extra_edges if agent_id not in edge}
                graph.object_access = {key for key in graph.object_access if key[0] != agent_id}
                graph.assignments = {task: owner for task, owner in graph.assignments.items() if owner != agent_id}
            else:
                raise ValueError(f"unsupported_graph_operation:{kind}")
        if len(self.instances | agents.keys()) > self.max_total_instances:
            raise ValueError("total_instance_limit")
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
        if (graph.agents != self.graph.agents or graph.extra_edges != self.graph.extra_edges or
                graph.object_access != self.graph.object_access or graph.assignments != self.graph.assignments):
            graph.version += 1
        if graph.version != self.graph.version and self.applied >= self.reconfiguration_limit:
            raise ValueError("effective_graph_patch_limit_exhausted")
        return graph

    def apply(self, patch):
        graph = self.preview(patch)
        previous = self.graph.version
        if graph.version != previous and self.applied >= self.reconfiguration_limit:
            raise RuntimeError("configured_effective_graph_patch_limit_exhausted")
        if graph.object_access != self.graph.object_access:
            self.object_epoch += 1
        self.install(graph)
        self.instances.update(a.agent_id for a in graph.agents)
        self.max_active = max(self.max_active, len(graph.agents))
        self.applied += int(graph.version != previous)
        self.trace.log("topology_patch_applied", patch_id=patch.patch_id,
                                  previous_version=previous, graph_version=graph.version,
                                  operations=[{**op,"role":asdict(op["role"])} if op["op"] == "SET_ROLE" else op
                                              for op in patch.operations], assignments=graph.assignments,
                                  delivery_plans=patch.delivery_plans,
                                  observation_sequence=patch.base_observation_sequence,
                                  evidence_ids=patch.evidence_ids)

    def add_object(self, ref, writer, *, task_id=None, status="output_available", **metadata):
        evidence = self.provenance.items[ref.object_id]
        if ref.key in self.objects:
            raise ValueError("immutable_object_already_registered")
        if writer not in self.instances:
            raise ValueError("unregistered_object_author")
        self.objects[ref.key] = {"object_id":ref.object_id,"version":ref.version,"view_id":ref.view_id,
            "writer":writer,"task_id":task_id,"status":status,"parent_ids":evidence.parent_ids,
            "source_trust":evidence.trust, **metadata}
        self.graph.object_access.add((writer,ref.key))
        self.object_epoch += 1
        self.trace.log("runtime_object_created", **self.objects[ref.key])

    def check_delivery(self, consumer_id, ref):
        self.graph.agent(consumer_id)
        obj=self.objects[ref.key]
        if obj["version"] != ref.version or obj["view_id"] != ref.view_id:
            raise ValueError("object_version_mismatch")
        if obj["status"] in {"held","quarantined"}:
            raise PermissionError("object_held")
        if (consumer_id,ref.key) not in self.graph.object_access:
            raise PermissionError("object_access_denied")
        author=obj["writer"]
        if author not in self.instances:
            raise PermissionError("unknown_original_author")
        active={a.agent_id for a in self.graph.agents}
        if author in active and author != consumer_id and (author,consumer_id) not in self.graph.extra_edges:
            raise PermissionError("object_delivery_route_denied")
        return "persistent_store" if author not in active else "live_route"

    def authorize_stored_delivery(self, consumer_id, ref, expected_stamp):
        if expected_stamp != self.stamp():
            raise ValueError("stale_stored_delivery")
        if self.objects[ref.key]["writer"] in {a.agent_id for a in self.graph.agents}:
            raise ValueError("stored_transport_requires_retired_author")
        if self.check_delivery(consumer_id, ref) != "persistent_store":
            raise PermissionError("stored_delivery_denied")

    def record_flow(self, kind, **data):
        row={"flow_sequence":len(self.flow_events)+1,"flow_kind":kind,**data}
        self.flow_events.append(row)
        self.trace.log("information_flow",**row)
        return row

    def record_submission(self, instance_id, activation_id, object_ids, request_id):
        self.exposures.setdefault(instance_id,set()).update(object_ids)
        self.record_flow("model_submitted",instance_id=instance_id,activation_id=activation_id,
                         object_ids=tuple(object_ids),request_id=request_id)
