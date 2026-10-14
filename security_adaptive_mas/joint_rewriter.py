"""Bounded, evidence-linked local rewrites of the one effective runtime graph.

The estimates here describe observable immediate work. Unknown remaining model
work stays unknown; a smaller graph is not treated as a token saving.
"""
from __future__ import annotations

from dataclasses import dataclass

from .graph_evaluation import coverage, project_risk
from .graph_runtime import TopologyPatch


MAX_CANDIDATES = 8
MAX_REPAIR_NEEDS = 2


@dataclass(frozen=True)
class JointTrigger:
    reconsider: bool
    reasons: tuple[str, ...]
    observation_sequence: int
    graph_version: int
    task_revision: int
    affected_need_ids: tuple[str, ...]
    affected_agent_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    cost_status: str


def joint_trigger(observation, analysis, runtime, *, task_delta=False,
                  output_arrived=False, risk_events=(), cost_opportunity=None,
                  lifecycle_completion=False):
    """One cheap trigger for delivered work, risk and measured context reuse."""
    reasons = []
    if task_delta:
        reasons.append("task_revision_changed")
    if output_arrived:
        reasons.append("worker_output_available")
    flagged = tuple(dict.fromkeys(event.get("agent_id") for event in risk_events
                                 if event.get("suspicion") == "flagged" and event.get("agent_id")))
    if flagged:
        reasons.append("observed_risk_boundary")
    if cost_opportunity is not None and cost_opportunity > 0:
        reasons.append("observed_repeated_payload")
    if lifecycle_completion:
        reasons.append("worker_lifecycle_completion")
    return JointTrigger(bool(reasons), tuple(reasons), observation.sequence,
                        runtime.graph.version, runtime.controller.task_revision,
                        tuple(need.requirement_id or need.description
                              for need in analysis.information_requirements),
                        flagged, observation.evidence_ids,
                        "observed_proxy" if cost_opportunity is not None else "unknown")


def _repair_candidate(runtime, analysis, patch, missing, suspects):
    """Use only an existing, independently produced clean object.

    A new summary or a renamed suspect object is not an independent provider.
    No speculative ARE read or LLM verification is hidden in this patch.
    """
    if len(missing) > MAX_REPAIR_NEEDS:
        return None, "repair_depth_exceeded"
    needs = {(item.task_id, item.requirement_id): item
             for item in analysis.information_requirements}
    graph = runtime.preview(patch)
    agents = {agent.agent_id for agent in graph.agents}
    operations = list(patch.operations)
    plans = list(patch.delivery_plans)
    for row in missing:
        need = needs.get((row["task_id"], row["requirement_id"]))
        if need is None or need.provider_task_id is None:
            return None, "need_has_no_registered_provider"
        consumer = graph.assignments.get(need.task_id, "agent_0")
        alternatives = [obj for obj in runtime.objects.values()
                        if obj["task_id"] == need.provider_task_id
                        and obj["status"] == "output_available"
                        and obj["writer"] in agents and obj["writer"] not in suspects
                        and not obj["low_trust"]]
        if not alternatives:
            return None, "no_independent_clean_object"
        chosen = min(alternatives, key=lambda obj: obj["object_id"])
        writer, object_id = chosen["writer"], chosen["object_id"]
        if writer != consumer and (writer, consumer) not in graph.extra_edges:
            op = {"op": "ADD_ROUTE", "source": writer, "target": consumer}
            operations.append(op)
            graph.extra_edges.add((writer, consumer))
        if (consumer, object_id) not in graph.object_access:
            operations.append({"op": "SET_OBJECT_ACCESS", "agent_id": consumer,
                               "object_id": object_id, "allowed": True})
            graph.object_access.add((consumer, object_id))
        if (consumer, object_id) not in plans:
            plans.append((consumer, object_id))
    repaired = TopologyPatch("repair_" + patch.patch_id, patch.base_graph_version,
                             patch.base_observation_sequence, patch.base_task_revision,
                             tuple(operations), delivery_plans=tuple(plans),
                             reason="existing_independent_object_repairs_need",
                             evidence_ids=patch.evidence_ids)
    return repaired, None


def _cost_observation(patch, runtime, budget):
    immediate_chars = sum(len(runtime.controller.provenance.items[object_id].text)
                          for _, object_id in patch.delivery_plans)
    planned_workers = sum(op["op"] == "ASSIGN" and op["agent_id"] != "agent_0"
                          for op in patch.operations)
    reference = None if budget is None else budget.actual_usage_by_purpose.get("coordinator")
    return {"known_delivery_characters": immediate_chars,
            "planned_worker_requests_lower_bound": planned_workers,
            "measured_prior_coordinator_tokens": None if not reference else reference["input_tokens"],
            "estimated_remaining_tokens": None,
            "estimated_cost_range": None,
            "cost_status": "unknown_future",
            "uncertainty_note": "remaining model calls and tokenized prompt size are not calibrated"}


def _exact_duplicate_consolidation(runtime, patch):
    """Avoid delivering a second identical artifact for the same producer task."""
    seen = set()
    omitted = set()
    for _, object_id in patch.delivery_plans:
        obj = runtime.objects[object_id]
        key = (obj["task_id"], runtime.controller.provenance.items[object_id].text)
        if obj["task_id"] is None or key not in seen:
            seen.add(key)
        else:
            omitted.add(object_id)
    if not omitted:
        return None
    operations = tuple(op for op in patch.operations
                       if not (op["op"] == "SET_OBJECT_ACCESS" and op.get("object_id") in omitted
                               and op.get("allowed")))
    plans = tuple(plan for plan in patch.delivery_plans if plan[1] not in omitted)
    return TopologyPatch("consolidate_" + patch.patch_id, patch.base_graph_version,
                         patch.base_observation_sequence, patch.base_task_revision,
                         operations, delivery_plans=plans,
                         reason="cost_consolidation_exact_duplicate",
                         evidence_ids=patch.evidence_ids)


def evaluate_candidates(controller, runtime, candidates, *, policy, protected_targets):
    """Preview executable patches, repair known gaps, then assess all dimensions."""
    observation = controller.online_snapshot()
    analysis = controller._current_task_analysis(observation)
    suspects = set(getattr(controller, "suspect_instances", ()))
    sources = {key for key, obj in runtime.objects.items()
               if obj["low_trust"] and controller.provenance.items[key].source != "startup"
               and (not suspects or obj["writer"] in suspects)}
    keep = next((patch for patch in candidates if patch.patch_id == "keep"), None)
    baseline = runtime.preview(keep) if keep else runtime.graph
    queue = list(candidates[:MAX_CANDIDATES])
    if policy in {"joint", "task_only", "task_plus_filter"}:
        for patch in tuple(queue):
            if len(queue) >= MAX_CANDIDATES:
                break
            compact = _exact_duplicate_consolidation(runtime, patch)
            if compact is not None:
                queue.append(compact)
    rows = []
    seen = {patch.patch_id for patch in queue}
    for patch in queue:
        try:
            graph = runtime.preview(patch)
            task = coverage(analysis, graph, runtime.objects)
            risk = project_risk(graph, runtime.objects, sources, suspects,
                                protected_targets, baseline_graph=baseline)
            cost = _cost_observation(patch, runtime, controller.run_budget)
            status = "feasible" if not task["known_missing"] else "unsatisfied_need"
            rows.append({"candidate_id": patch.patch_id, "patch": patch,
                         "task_coverage": task, "risk": risk, "cost": cost,
                         "status": status, "unsupported_reason": None})
            if (status == "unsatisfied_need" and policy == "joint"
                    and len(queue) < MAX_CANDIDATES):
                repaired, reason = _repair_candidate(runtime, analysis, patch,
                                                     task["known_missing"], suspects)
                if repaired and repaired.patch_id not in seen:
                    queue.append(repaired)
                    seen.add(repaired.patch_id)
                elif reason:
                    rows[-1]["unsupported_reason"] = reason
        except (KeyError, ValueError, PermissionError) as error:
            rows.append({"candidate_id": patch.patch_id, "patch": patch,
                         "task_coverage": None, "risk": None, "cost": None,
                         "status": "unsupported_candidate",
                         "unsupported_reason": str(error)})
    return rows


def select_candidate(rows, *, policy):
    """Task feasibility first; simple isolation baselines expose unmet needs."""
    if policy == "reactive_isolation":
        isolated = [row for row in rows if row["patch"].reason == "suspicious_worker_output_withheld"
                    and row["status"] in {"feasible", "unsatisfied_need"}]
        if isolated:
            chosen = min(isolated, key=lambda row: (len(row["risk"]["reachable_targets"] or ()),
                                                    row["candidate_id"]))
            return chosen, "simple_reactive_isolation_task_need_" + chosen["status"]
    if policy == "task_plus_filter":
        proposed, _ = select_candidate(rows, policy="task_only")
        keep = next((row for row in rows if row["candidate_id"] == "keep"
                     and row["risk"] is not None), None)
        if keep and len(proposed["risk"]["reachable_targets"] or ()) > len(keep["risk"]["reachable_targets"] or ()):
            contain = next((row for row in rows
                            if row["patch"].reason == "suspicious_worker_output_withheld"
                            and row["status"] in {"feasible", "unsatisfied_need"}
                            and len(row["risk"]["reachable_targets"] or ()) <= len(keep["risk"]["reachable_targets"] or ())), None)
            fallback = contain or keep
            return fallback, "posthoc_risk_filter_task_need_" + fallback["status"]
        return proposed, "task_proposal_passed_posthoc_risk_filter"
    feasible = [row for row in rows if row["status"] == "feasible"]
    if not feasible:
        raise RuntimeError("no_task_covering_candidate")
    if policy == "joint":
        counts = {row["candidate_id"]: len(row["risk"]["reachable_targets"] or ())
                  for row in feasible}
        floor = min(counts.values())
        if len(set(counts.values())) > 1:
            feasible = [row for row in feasible if counts[row["candidate_id"]] == floor]
    # Known immediate payload is comparable; uncalibrated future calls are not.
    # Explicit read contracts may be explored once within the shared request cap.
    worker = [row for row in feasible if row["cost"]["planned_worker_requests_lower_bound"]]
    if worker and policy in {"joint", "task_only", "task_plus_filter", "reactive_isolation"}:
        return min(worker, key=lambda row: (row["cost"]["known_delivery_characters"],
                                            row["candidate_id"])), "bounded_read_exploration_cost_unknown"
    preferred = [row for row in feasible if row["patch"].reason in {
        "release_output_available", "retire_output_available",
        "existing_independent_object_repairs_need", "cost_consolidation_exact_duplicate"}]
    if preferred:
        return min(preferred, key=lambda row: (row["cost"]["known_delivery_characters"],
                                               row["candidate_id"])), "task_coverage_with_observed_payload"
    return min(feasible, key=lambda row: (0 if row["candidate_id"] == "keep" else 1,
                                          row["cost"]["known_delivery_characters"],
                                          row["candidate_id"])), "no_supported_cost_difference"
