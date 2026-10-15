"""Topology proposals are graph objects, separate from executable ARE plans."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol
import itertools
import math
import random

from .online_task import TaskAnalysis


@dataclass(frozen=True)
class AgentPlan:
    agent_id: str
    role: str
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskAssignment:
    task_id: str
    agent_id: str


@dataclass(frozen=True)
class InformationDelivery:
    source_agent_id: str
    target_agent_id: str
    task_id: str


@dataclass(frozen=True)
class TopologyCandidate:
    candidate_id: str
    agents: tuple[AgentPlan, ...]
    assignments: tuple[TaskAssignment, ...]
    edges: tuple[tuple[str, str], ...]
    deliveries: tuple[InformationDelivery, ...]
    source: str
    evidence_ids: tuple[str, ...]
    protocol: str = "none"
    template: str | None = None
    runtime_support: str = "planning_only"


class CandidateGenerator(Protocol):
    def generate(self, analysis: TaskAnalysis) -> tuple[TopologyCandidate, ...]: ...


class TopologyValidator:
    def __init__(self, max_agents: int, authorized_tools: frozenset[str],
                 supported_protocols: frozenset[str] = frozenset({"none"}),
                 allowed_runtime_support: frozenset[str] = frozenset({"planning_only"})):
        self.max_agents = max_agents
        self.authorized_tools = authorized_tools
        self.supported_protocols = supported_protocols
        self.allowed_runtime_support = allowed_runtime_support

    def validate(self, candidate: TopologyCandidate, analysis: TaskAnalysis) -> None:
        ids = [agent.agent_id for agent in candidate.agents]
        if not candidate.candidate_id or not ids or len(ids) != len(set(ids)) or len(ids) > self.max_agents:
            raise ValueError("Invalid agent set or resource limit")
        if any(not agent.agent_id or not agent.role or
               not set(agent.allowed_tools) <= self.authorized_tools for agent in candidate.agents):
            raise ValueError("Invalid agent or tool authorization")
        if candidate.protocol not in self.supported_protocols:
            raise ValueError("Unsupported execution protocol")
        if not set(candidate.evidence_ids) <= set(analysis.evidence_ids):
            raise ValueError("Candidate cites unseen planning evidence")
        edges = set(candidate.edges)
        if len(edges) != len(candidate.edges) or any(a not in ids or b not in ids or a == b for a, b in edges):
            raise ValueError("Invalid communication edge")
        task_ids = {task.task_id for task in analysis.subtasks}
        for assignment in candidate.assignments:
            if assignment.agent_id not in ids or assignment.task_id not in task_ids:
                raise ValueError("Invalid task assignment")
        if len({item.task_id for item in candidate.assignments}) != len(candidate.assignments):
            raise ValueError("Task assigned more than once")
        if {item.task_id for item in candidate.assignments} != task_ids:
            raise ValueError("Not every visible task is assigned")
        for delivery in candidate.deliveries:
            if (delivery.source_agent_id, delivery.target_agent_id) not in edges or delivery.task_id not in task_ids:
                raise ValueError("Invalid information delivery")
        if candidate.runtime_support not in {"planning_only", "controller_spec_only",
                                              "are_single_read_only", "are_dynamic_read_only"}:
            raise ValueError("Unknown runtime support claim")
        if candidate.runtime_support not in self.allowed_runtime_support:
            raise ValueError("Runtime cannot execute this candidate")


class TemplateCandidateGenerator:
    """Deterministic structural baseline, not a learned performance method."""

    def __init__(self, max_agents: int = 3):
        self.max_agents = max_agents

    def generate(self, analysis: TaskAnalysis) -> tuple[TopologyCandidate, ...]:
        count = min(self.max_agents, max(1, len(analysis.subtasks) + 1))
        ids = tuple(f"agent_{i}" for i in range(count))
        patterns = ("singleton",) if count == 1 else ("singleton", "star", "chain", "fully_connected", "tree")
        candidates = []
        for pattern in patterns:
            members = ids[:1] if pattern == "singleton" else ids
            if pattern == "singleton":
                edges = ()
            elif pattern in {"star", "tree"}:
                edges = tuple((a, b) for member in members[1:] for a, b in ((members[0], member), (member, members[0])))
            elif pattern == "chain":
                edges = tuple((a, b) for left, right in zip(members, members[1:])
                              for a, b in ((left, right), (right, left)))
            else:
                edges = tuple((a, b) for a in members for b in members if a != b)
            assignments = tuple(TaskAssignment(task.task_id, members[i % len(members)])
                                for i, task in enumerate(analysis.subtasks))
            candidates.append(TopologyCandidate(
                f"template:{pattern}:{analysis.as_of_sequence}",
                tuple(AgentPlan(agent, "coordinator" if i == 0 else "worker")
                      for i, agent in enumerate(members)), assignments, edges, (),
                "template_baseline", analysis.evidence_ids, template=pattern,
            ))
        return tuple(candidates)


def candidate_from_manta(spec, assignments: tuple[TaskAssignment, ...],
                         analysis: TaskAnalysis) -> TopologyCandidate:
    """Adapter boundary for existing MANTA spec; no claim of ARE execution."""
    from .organization import communication_edges

    return TopologyCandidate(
        f"manta:{spec.version}",
        tuple(AgentPlan(node.agent_id, node.structural_role or "worker",
                        tuple(node.allowed_tools or ())) for node in spec.agents),
        assignments, tuple(sorted(communication_edges(spec))), (),
        "manta_adapter", analysis.evidence_ids,
        template=spec.group(spec.root_group_id).pattern,
        runtime_support="controller_spec_only",
    )


class MantaCandidateGenerator:
    """Wrap the existing MANTA kernel planner as an interchangeable proposer."""

    def __init__(self, planner, authorized_tools: frozenset[str],
                 read_tools: frozenset[str] = frozenset()):
        self.planner = planner
        self.authorized_tools = authorized_tools
        self.read_tools = read_tools

    def generate(self, analysis: TaskAnalysis) -> tuple[TopologyCandidate, ...]:
        descriptions = tuple(task.description for task in analysis.subtasks)
        candidates = []
        for pattern in ("singleton", "star", "chain"):
            spec, _ = self.planner.make(pattern, analysis.as_of_sequence + 1,
                                        self.authorized_tools, descriptions,
                                        read_tools=self.read_tools)
            ids = tuple(node.agent_id for node in spec.agents)
            assignments = tuple(TaskAssignment(task.task_id, ids[i % len(ids)])
                                for i, task in enumerate(analysis.subtasks))
            candidate = candidate_from_manta(spec, assignments, analysis)
            candidate = replace(candidate, candidate_id=f"manta:{pattern}:{spec.version}")
            candidates.append(candidate)
        return tuple(candidates)


@dataclass(frozen=True)
class PerformanceEstimate:
    candidate_id: str
    benefit: float | None
    communication_edges: int
    agent_count: int
    evidence_ids: tuple[str, ...]


class PerformanceEvaluator(Protocol):
    def evaluate(self, candidate: TopologyCandidate, analysis: TaskAnalysis) -> PerformanceEstimate: ...


class BaselinePerformanceEvaluator:
    def evaluate(self, candidate: TopologyCandidate, analysis: TaskAnalysis) -> PerformanceEstimate:
        return PerformanceEstimate(candidate.candidate_id, None, len(candidate.edges),
                                   len(candidate.agents), analysis.evidence_ids)


class TopologySelectorProtocol(Protocol):
    def select(self, candidates: tuple[TopologyCandidate, ...],
               estimates: tuple[PerformanceEstimate, ...],
               current_id: str | None = None) -> TopologyCandidate | None: ...


class TopologySelector:
    """Conservative baseline: retain current topology without estimated benefit."""

    def select(self, candidates: tuple[TopologyCandidate, ...],
               estimates: tuple[PerformanceEstimate, ...],
               current_id: str | None = None) -> TopologyCandidate | None:
        valid = {candidate.candidate_id for candidate in candidates}
        if len(valid) != len(candidates) or {item.candidate_id for item in estimates} != valid:
            raise ValueError("Estimates do not match candidates")
        if current_id in valid:
            return next(candidate for candidate in candidates if candidate.candidate_id == current_id)
        if any(item.benefit is None for item in estimates):
            return None
        best = max(estimates, key=lambda item: (item.benefit, -item.communication_edges, -item.agent_count))
        return next(candidate for candidate in candidates if candidate.candidate_id == best.candidate_id)


TOPOLOGY_FAMILIES = ("singleton", "star", "directed_chain", "fork_join", "hierarchical_tree",
                     "ring", "fully_connected", "diamond", "clustered", "small_world",
                     "hub_biased", "geometric_delaunay", "task_dag")


def generate_edges(family, members, *, assignments, dependencies, parameters, seed):
    """Explicit directed structures; at most eight nodes. No template substitution."""
    from .organization_contracts import UnsupportedDesign
    if family not in TOPOLOGY_FAMILIES:
        raise ValueError(f"unknown_topology:{family}")
    ids = tuple(members)
    n = len(ids)
    if len(set(ids)) != n or not 1 <= n <= 8:
        raise ValueError("invalid_topology_members")
    rng = random.Random(seed)
    edges = set()
    def both(a, b):
        edges.update(((a, b), (b, a)))
    if family == "singleton":
        if n != 1:
            raise UnsupportedDesign("singleton_requires_one_instance")
    elif n < 2:
        raise UnsupportedDesign(f"{family}_requires_multiple_instances")
    elif family == "star":
        center = "agent_0" if "agent_0" in ids else ids[0]
        for node in ids:
            if node != center:
                both(center, node)
    elif family == "directed_chain":
        edges.update(zip(ids, ids[1:]))
    elif family in {"fork_join", "diamond"}:
        if n < 4:
            raise UnsupportedDesign(f"{family}_requires_at_least_four_instances")
        branches = ids[1:-1] if family == "fork_join" else ids[1:3]
        for node in branches:
            edges.update(((ids[0], node), (node, ids[-1] if family == "fork_join" else ids[3])))
        if family == "diamond":
            edges.update(zip(ids[3:], ids[4:]))
    elif family == "hierarchical_tree":
        ordered = ("agent_0",) + tuple(x for x in ids if x != "agent_0") if "agent_0" in ids else ids
        for index in range(1, n):
            both(ordered[(index - 1) // 2], ordered[index])
    elif family == "ring":
        edges.update((node, ids[(index + 1) % n]) for index, node in enumerate(ids))
    elif family == "fully_connected":
        edges.update((a, b) for a in ids for b in ids if a != b)
    elif family == "clustered":
        size = parameters.get("cluster_size", 2)
        if type(size) is not int or size < 2:
            raise ValueError("invalid_cluster_size")
        groups = [ids[i:i+size] for i in range(0, n, size)]
        for group in groups:
            edges.update((a, b) for a in group for b in group if a != b)
        for left, right in zip(groups, groups[1:]):
            both(left[-1], right[0])
    elif family == "small_world":
        if n < 4:
            raise UnsupportedDesign("small_world_requires_four_instances")
        k, p = parameters.get("neighbors", 2), parameters.get("shortcut_probability", .25)
        if type(k) is not int or not 1 <= k < n or not 0 <= p <= 1:
            raise ValueError("invalid_small_world_parameters")
        for i, node in enumerate(ids):
            for offset in range(1, k+1):
                edges.add((node, ids[(i+offset) % n]))
            if rng.random() < p:
                choices = [x for x in ids if x != node and (node, x) not in edges]
                if choices:
                    edges.add((node, rng.choice(choices)))
    elif family == "hub_biased":
        both(ids[0], ids[1])
        degree = {ids[0]: 1, ids[1]: 1}
        for node in ids[2:]:
            parent = rng.choices(list(degree), weights=[v+1 for v in degree.values()])[0]
            both(parent, node)
            degree[parent] += 1
            degree[node] = 1
    elif family == "geometric_delaunay":
        if n < 3:
            raise UnsupportedDesign("delaunay_requires_three_noncollinear_points")
        points = parameters.get("coordinates")
        points = [(rng.random(), rng.random()) for _ in ids] if points is None else points
        if len(points) != n or any(len(p) != 2 or not all(math.isfinite(x) for x in p) for p in points):
            raise ValueError("invalid_delaunay_coordinates")
        # Exhaustive empty-circumcircle algorithm is bounded (C(8,3) triples).
        triangles = 0
        for a, b, c in itertools.combinations(range(n), 3):
            ax, ay = points[a]; bx, by = points[b]; cx, cy = points[c]
            determinant = 2*(ax*(by-cy)+bx*(cy-ay)+cx*(ay-by))
            if abs(determinant) < 1e-12:
                continue
            aa, bb, cc = ax*ax+ay*ay, bx*bx+by*by, cx*cx+cy*cy
            ux = (aa*(by-cy)+bb*(cy-ay)+cc*(ay-by))/determinant
            uy = (aa*(cx-bx)+bb*(ax-cx)+cc*(bx-ax))/determinant
            radius = (ux-ax)**2+(uy-ay)**2
            distances = [(ux-x)**2+(uy-y)**2-radius for j,(x,y) in enumerate(points) if j not in (a,b,c)]
            if any(abs(d) < 1e-12 for d in distances):
                raise UnsupportedDesign("delaunay_cocircular_coordinates_require_explicit_perturbation")
            if all(d > 0 for d in distances):
                triangles += 1
                for i,j in ((a,b),(b,c),(a,c)):
                    both(ids[i], ids[j])
        if not triangles:
            raise UnsupportedDesign("delaunay_collinear_coordinates")
    elif family == "task_dag":
        for dependency in dependencies:
            if dependency.certainty.value == "unknown":
                raise UnsupportedDesign("task_dag_has_unknown_dependency")
            if dependency.predecessor_id == "root":
                continue
            a, b = assignments[dependency.predecessor_id], assignments[dependency.successor_id]
            if a != b:
                edges.add((a,b))
        producers = {a for a,b in edges}
        if "agent_0" in ids:
            edges.update((a,"agent_0") for a in ids if a != "agent_0" and a not in producers)
    return tuple(sorted(edges))


def compile_activations(role_specs, dependencies, edges, protocol, rounds, plan_id):
    from .organization_contracts import Activation, UnsupportedDesign
    owners = {role.task_id: instance for instance, role in role_specs}
    tasks = tuple(owners)
    prerequisites = {task: set() for task in tasks}
    for dependency in dependencies:
        if dependency.successor_id not in owners or dependency.predecessor_id == "root":
            continue
        if dependency.certainty.value == "unknown":
            raise UnsupportedDesign("activation_has_unknown_dependency")
        if dependency.predecessor_id not in owners:
            raise UnsupportedDesign("activation_provider_not_in_plan")
        a,b = owners[dependency.predecessor_id], owners[dependency.successor_id]
        if a != b and (a,b) not in edges:
            raise UnsupportedDesign("topology_cannot_deliver_dependency")
        prerequisites[dependency.successor_id].add(dependency.predecessor_id)
    ordered = []
    remaining = set(tasks)
    while remaining:
        ready = sorted(t for t in remaining if prerequisites[t] <= set(ordered))
        if not ready:
            raise ValueError("activation_dependency_cycle")
        ordered.extend(ready)
        remaining.difference_update(ready)
    count = 1 if protocol == "dependency_schedule" else 2 if protocol == "one_round_exchange" else rounds
    activations = []
    for round_index in range(count):
        for task in ordered:
            before = [f"{plan_id}:{round_index}:{t}" for t in prerequisites[task]]
            if round_index:
                before.extend(a.activation_id for a in activations if a.round_index == round_index-1)
            activations.append(Activation(f"{plan_id}:{round_index}:{task}", owners[task], task,
                                          round_index, tuple(before)))
    return tuple(activations)
