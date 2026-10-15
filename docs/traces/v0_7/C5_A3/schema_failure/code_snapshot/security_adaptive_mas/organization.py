from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .manta_adapter import load_manta_spec


@dataclass(frozen=True)
class TaskState:
    task_text: str
    pending_subtasks: tuple[str, ...]
    phase: str = "intake"
    observed_tool_calls: int = 0
    risky_input: bool = False


class TaskAnalyzer:
    """Analyze current task and observed events only; no scenario/validator access."""

    def analyze(self, task_text: str, pending_subtasks: tuple[str, ...],
                phase: str, observed_tool_calls: int, risky_input: bool = False) -> TaskState:
        if phase not in {"intake", "parallel", "commit"}:
            raise ValueError("Unknown task phase")
        return TaskState(task_text, pending_subtasks, phase, observed_tool_calls, risky_input)


@dataclass(frozen=True)
class Candidate:
    pattern: str
    spec: Any
    assignments: dict[str, str]
    score: float
    checks: tuple[str, ...]
    reason: str


class TopologyPlanner:
    def __init__(self, manta_root, max_agents: int):
        self.m = load_manta_spec(manta_root)
        self.max_agents = max_agents

    def make(self, pattern: str, version: int, authorized_tools: frozenset[str],
             subtasks: tuple[str, ...] = (), restricted: bool = False,
             read_tools: frozenset[str] = frozenset()):
        if pattern not in {"singleton", "star", "chain"}:
            raise ValueError("Unsupported v1 topology")
        count = 1 if pattern == "singleton" else min(
            self.max_agents, max(3 if restricted else 2, len(subtasks) + 1))
        # MANTA's validated mutation operations create the topology. Intermediate
        # nodes have allowed_tools=None, so they are never published to a gateway.
        spec = self.m.TopologySpec(
            version=0,
            agents=(self.m.AgentNode("agent_0", "g_root", structural_role="coordinator"),),
            groups=(self.m.GroupSpec("g_root", "singleton", ("agent_0",)),),
            root_group_id="g_root", rationale="initial singleton",
        )
        for _ in range(count - 1):
            mutation = self.m.TopologyMutation(
                rationale=f"grow for {pattern}", target_failure_modes=(),
                ops=(self.m.MutationOp("add_agent", {"group_id": "g_root"}),),
            )
            spec = mutation.apply(spec, max_agents=self.max_agents)
        if pattern == "chain":
            mutation = self.m.TopologyMutation(
                rationale="ordered verification before commit", target_failure_modes=(),
                ops=(self.m.MutationOp("set_group_pattern", {"group_id": "g_root", "pattern": "chain"}),),
            )
            spec = mutation.apply(spec, max_agents=self.max_agents)
        agents = []
        assignments = {}
        ids = [node.agent_id for node in spec.agents]
        for index, node in enumerate(spec.agents):
            role = "coordinator" if index == 0 else ("verifier" if pattern == "chain" and index == len(ids) - 1 else "worker")
            tools = tuple(sorted(authorized_tools if index == 0 else read_tools))
            visible = tuple(i for i in ids if i != node.agent_id and (
                pattern == "star" and (index == 0 or i == ids[0])
                or pattern == "chain" and abs(index - ids.index(i)) == 1
            ))
            if pattern == "chain" and index == 0:
                visible = tuple(sorted(set(visible) | {ids[-1]}))
            if pattern == "chain" and index == len(ids) - 1:
                visible = tuple(sorted(set(visible) | {ids[0]}))
            policy = self.m.ContextPolicy(visible_from=visible, share_scope="parent_only",
                                          evidence_access="own", summary_only=index != 0,
                                          max_packet_chars=1000)
            agents.append(replace(node, structural_role=role, allowed_tools=tools, context=policy))
            assignments[node.agent_id] = (
                "coordinate and commit" if index == 0 else
                (subtasks[index - 1] if index - 1 < len(subtasks) else "verify current state")
            )
        spec = replace(spec, version=version, agents=tuple(agents),
                       rationale=f"{pattern}: task-derived plan")
        if pattern == "chain" and len(ids) > 2:
            mutation = self.m.TopologyMutation(
                rationale="direct authorized verification channel",
                target_failure_modes=("verifier_unreachable",),
                ops=(self.m.MutationOp("add_edge", {"src": ids[-1], "dst": ids[0]}),),
            )
            spec = replace(mutation.apply(spec, max_agents=self.max_agents), version=version)
        spec.validate(max_agents=self.max_agents)
        return spec, assignments


class OrganizationSelector:
    def __init__(self, planner: TopologyPlanner, weights: dict[str, float]):
        self.planner, self.weights = planner, weights

    def choose(self, state: TaskState, version: int,
               authorized_tools: frozenset[str],
               read_tools: frozenset[str] = frozenset()) -> tuple[Candidate, list[dict]]:
        trials = []
        candidates = []
        for pattern in ("singleton", "star", "chain"):
            spec, assignments = self.planner.make(
                pattern, version, authorized_tools, state.pending_subtasks,
                restricted=state.risky_input, read_tools=read_tools,
            )
            checks = []
            for node in spec.agents:
                if node.allowed_tools is None or not set(node.allowed_tools) <= authorized_tools:
                    checks.append("permission exceeds task authorization")
                if node.context.evidence_access == "global":
                    checks.append("unbounded evidence inheritance")
            benefit = (
                3 if pattern == "star" and state.phase == "parallel" and len(state.pending_subtasks) >= 2 else
                3 if pattern == "chain" and (state.phase == "commit" or state.risky_input) else
                2 if pattern == "singleton" and state.phase == "intake" else 0
            )
            cost = len(spec.agents) - 1
            risk = (2 if pattern == "star" and state.risky_input else 0)
            score = (self.weights["benefit"] * benefit -
                     self.weights["cost"] * cost - self.weights["risk"] * risk)
            trials.append({"pattern": pattern, "score": score, "checks": checks,
                           "benefit": benefit, "cost": cost, "risk_proxy": risk})
            if not checks:
                candidates.append(Candidate(pattern, spec, assignments, score,
                                            tuple(checks), f"phase={state.phase}; risk={state.risky_input}"))
        if not candidates:
            raise RuntimeError("No admissible organization")
        return max(candidates, key=lambda c: c.score), trials


def communication_edges(spec) -> frozenset[tuple[str, str]]:
    group = spec.group(spec.root_group_id)
    if group.pattern == "explicit":
        return frozenset(spec.extra_edges)
    ids = group.member_ids
    if group.pattern == "singleton":
        edges = set()
    elif group.pattern == "star":
        hub = group.leader_id
        edges = {(a, b) for a in ids for b in ids if a != b and hub in {a, b}}
    elif group.pattern == "chain":
        edges = {(a, b) for left, right in zip(ids, ids[1:]) for a, b in ((left, right), (right, left))}
    else:
        raise ValueError("Unsupported v1 topology")
    # MANTA treats extra edges as undirected; its mutation rejects the reverse
    # as a duplicate. The gateway must implement the same semantics.
    for src, dst in spec.extra_edges:
        edges.add((src, dst))
        edges.add((dst, src))
    return frozenset(edges)
