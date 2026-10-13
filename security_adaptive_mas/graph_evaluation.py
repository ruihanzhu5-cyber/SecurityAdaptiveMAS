"""AND input coverage and conservative live-object reachability, not ASR."""
from __future__ import annotations

from collections import deque


def coverage(analysis, graph, objects):
    rows = []
    for need in analysis.information_requirements:
        provider = need.provider_task_id
        available = [obj for obj in objects.values() if obj["task_id"] == provider and provider is not None and obj["status"] == "output_available"]
        consumer = graph.assignments.get(need.task_id, "agent_0")
        if available:
            status = "available" if any((consumer, obj["object_id"]) in graph.object_access for obj in available) else "blocked"
        elif provider in graph.assignments:
            producer = graph.assignments[provider]
            status = "producible" if producer == consumer or (producer, consumer) in graph.extra_edges else "blocked"
        else:
            status = "unknown"
        rows.append({"task_id": need.task_id, "requirement_id": need.requirement_id, "status": status})
    return {"requirements": rows, "known_missing": [row for row in rows if row["status"] == "blocked"],
            "unknown": [row for row in rows if row["status"] == "unknown"]}


def _reachability(graph, objects, sources, suspects):
    edges = set(graph.extra_edges)
    for agent, object_id in graph.object_access:
        edges.add((object_id, agent))
    for obj in objects.values():
        edges.add((obj["writer"], obj["object_id"]))
        edges.update((parent, obj["object_id"]) for parent in obj["parent_ids"])
    for agent in graph.agents:
        edges.update((agent.agent_id, "tool:" + name) for name in agent.allowed_tools)
    adjacency = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)

    def reach(roots):
        paths = {root: [root] for root in roots}
        queue = deque(roots)
        while queue:
            source = queue.popleft()
            for target in sorted(adjacency.get(source, ())):
                if target not in paths:
                    paths[target] = paths[source] + [target]
                    queue.append(target)
        return paths

    return reach(sources), reach(suspects)


def project_risk(graph, objects, sources, suspects, protected_targets, baseline_graph=None):
    """Conservative live-object reachability, not ASR.

    When ``baseline_graph`` is supplied (typically the KEEP candidate), the
    reachable-target sets are compared to report what the candidate newly
    exposes or no longer exposes relative to the baseline.
    """
    exposure_paths, suspect_paths = _reachability(graph, objects, sources, suspects)
    reachable = set(protected_targets) & exposure_paths.keys() if protected_targets else set()
    witness_paths = {target: exposure_paths[target] for target in reachable}
    result = {"risk_model": "current_effective_graph_with_live_objects",
              "target_status": "configured" if protected_targets else "not_computable_no_targets",
              "reachable_targets": sorted(reachable) if protected_targets else None,
              "root_sources": sorted(sources),
              "suspect_successors": sorted(suspect_paths.keys() - set(suspects)) if suspects else None,
              "witness_paths": witness_paths,
              "risk_status": ("at_risk" if reachable else "no_risk") if protected_targets
                             else "not_computable_no_targets",
              "attack_success": None, "containment_success": None}
    if baseline_graph is not None and protected_targets:
        baseline_paths, _ = _reachability(baseline_graph, objects, sources, suspects)
        baseline_reachable = set(protected_targets) & baseline_paths.keys()
        newly = reachable - baseline_reachable
        no_longer = baseline_reachable - reachable
        result["newly_reachable_targets"] = sorted(newly)
        result["no_longer_reachable_targets"] = sorted(no_longer)
        result["delta_witness_paths"] = {target: witness_paths[target] for target in newly}
    return result
