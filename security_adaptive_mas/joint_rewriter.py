"""Bounded, evidence-linked local rewrites of the one effective runtime graph.

The estimates here describe observable immediate work. Unknown remaining model
work stays unknown; a smaller graph is not treated as a token saving.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict, replace
from uuid import uuid4
import time

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
    flagged = tuple(dict.fromkeys(event["subject_id"] for event in risk_events
                                 if event["suspicion"] in {"flagged", "supported"}))
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


def plan_patch(runtime, plan):
    return TopologyPatch(plan.plan_id, plan.base_graph_version, plan.base_observation_sequence,
                         plan.base_task_revision, plan.immediate_operations,
                         assignments=plan.assignments,
                         delivery_plans=plan.existing_object_deliveries,
                         reason=plan.intervention,evidence_ids=plan.reason_evidence_ids)


def generate_rewrite_plans(controller, runtime, roles):
    started = time.monotonic()
    from .organization_contracts import RewritePlan, UnsupportedDesign
    from .topology_candidates import generate_edges, compile_activations
    observation = controller.online_snapshot()
    analysis = controller._current_task_analysis(observation)
    design = controller.design
    if design["method"] == "reactive_isolation":
        repair_roles = {row["role_id"] for row in controller.repair_specs}
        roles = tuple(role for role in roles if role.role_id not in repair_roles)
    base = dict(base_graph_version=runtime.graph.version,base_observation_sequence=observation.sequence,
                base_task_revision=controller.task_revision,reason_evidence_ids=observation.evidence_ids)
    keep = RewritePlan("keep",**base,role_specs=tuple(("agent_0",r) for r in roles),
        assignments=tuple(runtime.graph.assignments.items()),topology_spec={"family":"current","edges":tuple(runtime.graph.extra_edges)},
        protocol_spec={"name":"dependency_schedule","rounds":1},immediate_operations=({"op":"KEEP"},),
        existing_object_deliveries=(),pending_work=tuple({"kind":"continue","task_id":r.task_id} for r in roles),
        future_output_bindings=(),release_conditions=())
    candidates = [keep]
    unsupported = []
    groups = {"task":[],"risk":[],"joint_cost":[]}
    suffix = uuid4().hex[:6]
    for family in design["topologies"]:
        if family == "singleton":
            # The official Coordinator keeps the same remaining goal; no shadow worker loop.
            singleton = replace(keep,plan_id="singleton_"+suffix,intervention="task",
                                topology_spec={"family":"singleton","edges":()})
            if len(runtime.graph.agents) == 1:
                groups["task"].append(singleton)
            else:
                unsupported.append({"family":family,"reason":"singleton_retirement_requires_resolving_live_work"})
            continue
        specs, operations, deliveries = [], [], []
        retained = {"agent_0"}
        for role in roles:
            independence_roots = tuple(root for repair in controller.repair_specs if repair["role_id"] == role.role_id
                                       for root in repair["independent_from"])
            existing = next((a for a in runtime.graph.agents if a.agent_id != "agent_0"
                and a.role_contract is not None and a.role_contract.role_id == role.role_id
                and a.agent_id not in controller.suspect_instances
                and not any(controller.provenance.derived_from(key,root)
                            for key in runtime.exposures.get(a.agent_id,()) for root in independence_roots)
                and set(role.requested_tools) <= a.allowed_tools),None)
            instance = existing.agent_id if existing else "role_"+suffix+"_"+role.role_id
            retained.add(instance)
            specs.append((instance,role))
            if existing is None:
                operations.append({"op":"SPAWN_WORKER","agent_id":instance,"tools":role.requested_tools})
            operations.extend(({"op":"SET_ROLE","agent_id":instance,"role":role},
                               {"op":"ASSIGN","agent_id":instance,"task_id":role.task_id}))
            for object_id in role.required_inputs:
                if object_id not in runtime.objects:
                    raise ValueError("role_input_has_no_runtime_object")
                operations.append({"op":"SET_OBJECT_ACCESS","agent_id":instance,"object_id":object_id,"allowed":True})
                deliveries.append((instance,object_id))
        for agent in runtime.graph.agents:
            if agent.agent_id not in retained:
                operations.append({"op":"RETIRE_WORKER","agent_id":agent.agent_id})
        owners = {r.task_id:i for i,r in specs}
        owners["root"] = "agent_0"
        members = tuple(i for i,r in specs)+("agent_0",)
        try:
            edges = generate_edges(family,members,assignments=owners,dependencies=analysis.dependencies,
                                   parameters=design["topology_parameters"],seed=design["seed"])
            # A delivered input must have an explicit live source route or persistent-store author.
            active_authors = {a.agent_id for a in runtime.graph.agents} & retained
            for consumer,object_id in deliveries:
                writer = runtime.objects[object_id]["writer"]
                if (controller.provenance.items[object_id].trust != "trusted_task" and
                        writer in active_authors and writer != consumer and (writer,consumer) not in edges):
                    raise UnsupportedDesign("topology_cannot_deliver_required_existing_input")
            name = family+"_"+suffix
            activations = compile_activations(tuple(specs),analysis.dependencies,edges,
                                             design["protocol"],design["rounds"],name)
        except UnsupportedDesign as error:
            unsupported.append({"family":family,"reason":str(error)})
            continue
        operations.extend({"op":"REMOVE_ROUTE","source":a,"target":b}
                          for a,b in runtime.graph.extra_edges if a in retained and b in retained)
        operations.extend({"op":"ADD_ROUTE","source":a,"target":b} for a,b in edges)
        bindings, pending = [], []
        for need in controller.need_specs.values():
            for provider in need.provider_alternatives:
                role = next((r for i,r in specs if r.task_id == provider["task_id"]),None)
                if role is None or not set(need.acceptance_contract["required_fields"]) <= set(role.output_contract):
                    continue
                if provider["output_slot"] not in {"result",*role.output_contract}:
                    continue
                if any(controller.provenance.derived_from(key,root)
                       for key in (*role.required_inputs,*role.source_evidence_ids)
                       for root in need.acceptance_contract["independent_from"]):
                    continue
                bindings.append({"need_id":need.need_id,"producer_task":role.task_id,
                    "output_slot":provider["output_slot"],"acceptance_contract":need.acceptance_contract})
            for repair in controller.repair_specs:
                if not design["ablations"]["repair"]:
                    continue
                if repair["need_id"] == need.need_id:
                    if repair["kind"] == "verify":
                        raise ValueError("OPEN_semantic_verification_acceptance_contract_unavailable")
                    role = next((r for i,r in specs if r.role_id == repair["role_id"]),None)
                    if role is None or not role.requested_tools and repair["kind"] == "requery":
                        continue
                    if any(controller.provenance.derived_from(key,root) for key in (*role.required_inputs,*role.source_evidence_ids)
                           for root in repair["independent_from"]):
                        continue
                    if repair["kind"] == "verify" and not repair["independent_from"]:
                        unsupported.append({"family":family,"reason":"verification_requires_declared_comparison_source"})
                        continue
                    pending.append(repair)
                    bindings.append({"need_id":need.need_id,"producer_task":role.task_id,
                        "output_slot":"result","acceptance_contract":need.acceptance_contract})
        if design["ablations"]["repair"]:
            # Existing providers qualify by a concrete check and field contract, never source label.
            for need in controller.need_specs.values():
                consumer = owners.get(need.consumer_task_id,"agent_0")
                for provider in need.provider_alternatives:
                    object_id = runtime.bindings.get((provider["task_id"],provider["output_slot"]))
                    if object_id is None or runtime.checks[object_id].get("artifact_schema_valid") is not True:
                        continue
                    obj = runtime.objects[object_id]
                    description = next(t.description for t in analysis.subtasks if t.task_id == provider["task_id"])
                    if obj["producer_task_description"] != description:
                        continue
                    if not set(need.acceptance_contract["required_fields"]) <= set(obj["fields"] or {}):
                        continue
                    if any(controller.provenance.derived_from(object_id,root)
                           for root in need.acceptance_contract["independent_from"]):
                        continue
                    if any(event["suspicion"] in {"supported","flagged"} and object_id in event.get("object_ids",())
                           for event in controller.framework_team.risk_events):
                        continue
                    writer = obj["writer"]
                    if writer in retained and writer != consumer and (writer,consumer) not in edges:
                        operations.append({"op":"ADD_ROUTE","source":writer,"target":consumer})
                        edges = tuple(sorted((*edges,(writer,consumer))))
                    operations.append({"op":"SET_OBJECT_ACCESS","agent_id":consumer,"object_id":object_id,"allowed":True})
                    if (consumer,object_id) not in deliveries:
                        deliveries.append((consumer,object_id))
                    pending.append({"kind":"existing_result","need_id":need.need_id,"object_id":object_id,
                                    "checker":runtime.checks[object_id]["checker"]})
                    break
        candidate = RewritePlan(name,**base,role_specs=tuple(specs),assignments=tuple(owners.items()),
            topology_spec={"family":family,"edges":edges,"seed":design["seed"]},
            protocol_spec={"name":design["protocol"],"rounds":max(a.round_index for a in activations)+1 if activations else 1},
            immediate_operations=tuple(operations),existing_object_deliveries=tuple(deliveries),
            pending_work=tuple(pending),future_output_bindings=tuple(bindings),
            release_conditions=tuple(controller.need_specs),activations=activations)
        groups["task"].append(candidate)
        if design["search_depth"] == 2:
            # Risk contraction only changes future routes/ACL; retained history is untouched.
            dangerous = set(controller.suspect_instances)
            contraction = [op for op in operations if not (op["op"] == "ADD_ROUTE" and op["source"] in dangerous)]
            for a,b in edges:
                if a in dangerous:
                    contraction.append({"op":"REMOVE_ROUTE","source":a,"target":b})
            contracted = replace(candidate,plan_id="isolate_"+name,intervention="risk",
                                 immediate_operations=tuple(contraction),
                                 pending_work=() if design["method"] == "reactive_isolation" else candidate.pending_work)
            groups["risk"].append(contracted)
            if candidate.protocol_spec["rounds"] > 1:
                compact_activations = compile_activations(tuple(specs),analysis.dependencies,edges,
                                                          "dependency_schedule",1,"compact_"+name)
                groups["joint_cost"].append(replace(contracted,plan_id="compact_"+name,
                    intervention="joint_cost",protocol_spec={"name":"dependency_schedule","rounds":1},
                    activations=compact_activations))
    seen = {keep.dedup_key()}
    # Round-robin preserves intervention diversity before the fixed resource cap.
    while any(groups.values()) and len(candidates) < design["max_candidates"]:
        for group in groups.values():
            if group and len(candidates) < design["max_candidates"]:
                candidate = group.pop(0)
                key = candidate.dedup_key()
                if key not in seen:
                    seen.add(key); candidates.append(candidate)
    controller.trace.log("rewrite_candidates_generated",candidate_ids=[p.plan_id for p in candidates],
                         unsupported_topologies=unsupported,limit=design["max_candidates"],
                         generation_wall_seconds=time.monotonic()-started)
    return candidates


def select_rewrite_plan(controller, runtime, plans):
    started = time.monotonic()
    from .cost_model import assess_cost
    from .graph_evaluation import plan_need_status, temporal_plan_risk
    design = controller.design
    observation = controller.online_snapshot()
    trigger = joint_trigger(observation,controller._current_task_analysis(observation),runtime,
        task_delta=controller.task_revision != controller.last_joint_task_revision,
        output_arrived=any(p.plan_id.startswith("release_") for p in plans),
        risk_events=controller.framework_team.risk_events[controller.last_joint_risk_event_count:],
        cost_opportunity=controller.observed_repeated_object_chars-controller.last_cost_reconsidered_chars)
    if not trigger.reconsider:
        controller.trace.log("rewrite_keep",reason="no_new_task_risk_cost_or_output_evidence")
        return next(p for p in plans if p.plan_id == "keep")
    rows = []
    for plan in plans:
        graph = runtime.preview(plan_patch(runtime,plan))
        needs = plan_need_status(controller,runtime,plan,graph)
        risk = temporal_plan_risk(runtime,plan,graph,controller.protected_targets)
        cost = assess_cost(plan,runtime,design)
        rows.append({"plan":replace(plan,cost_assessment=cost,risk_assessment=risk),
            "needs":needs,"risk_count":len(risk["suspected_source_reachability"])
                if risk["comparison_status"] == "comparable_registered_sources" else None,
            "unsupported_needs":[n for n in needs if n["status"] not in {"satisfied","planned"}],
            "satisfied_need_count":sum(n["status"] == "satisfied" for n in needs),
            "planned_need_count":sum(n["status"] == "planned" for n in needs),
            "unknown_need_count":sum(n["status"] == "unknown" for n in needs),
            "cost":cost,"risk":risk})
    keep = next(row for row in rows if row["plan"].plan_id == "keep")
    feasible = [row for row in rows if not row["unsupported_needs"]]
    method = design["method"]
    risk_unknown = method in {"joint","fixed_security","task_plus_filter"} and (
        keep["risk_count"] is None or any(row["risk_count"] is None for row in feasible))
    if risk_unknown:
        controller.trace.log("rewrite_blocked",status="UNDECIDABLE_RISK_COMPARISON",
                             reason="protected_targets_not_registered",alternatives=[r["risk"] for r in rows])
        raise RuntimeError("UNDECIDABLE_risk_comparison_no_protected_targets")
    if method in {"joint","fixed_security"}:
        feasible = [row for row in feasible if row["risk_count"] is not None and
                    keep["risk_count"] is not None and row["risk_count"] <= keep["risk_count"]]
    if method == "reactive_isolation" and controller.suspect_instances:
        feasible = [row for row in feasible if row["plan"].intervention == "risk"]
    if not feasible:
        controller.trace.log("rewrite_blocked",status="NO_SUPPORTED_PROVIDER",needs=[r["needs"] for r in rows])
        raise RuntimeError("NO_SUPPORTED_PROVIDER_or_no_feasible_rewrite")
    def ordering(row):
        cost = row["cost"]
        amount = (cost["predicted_remaining_tokens"] if design["cost_mode"] == "empirical"
                  else cost["remaining_declared_model_steps_upper_bound"])
        # Counts are diagnostics, not utility: extra deliveries/rounds earn no reward.
        # Prefer actual available required inputs to a future acquisition plan.
        risk = row["risk_count"] if method in {"joint","fixed_security","reactive_isolation"} else 0
        return (bool(row["planned_need_count"]),risk is None,risk or 0,
                amount,cost["declared_input_characters"],row["plan"].plan_id != "keep",
                row["plan"].plan_id)
    chosen = min(feasible,key=ordering)
    if method == "task_plus_filter" and (chosen["risk_count"] is None or keep["risk_count"] is None or
                                          chosen["risk_count"] > keep["risk_count"]):
        filtered = [row for row in feasible if row["risk_count"] is not None and
                    keep["risk_count"] is not None and row["risk_count"] <= keep["risk_count"]]
        if not filtered:
            raise RuntimeError("posthoc_filter_has_no_task_supported_plan")
        chosen = min(filtered,key=ordering)
    controller.trace.log("joint_decision",design_version="v0.5",candidate_id=chosen["plan"].plan_id,
        trigger_reasons=trigger.reasons,
        observation_sequence=chosen["plan"].base_observation_sequence,
        task_revision=controller.task_revision,organization_policy=method,
        selected_reason="executable_required_inputs_risk_then_cost_stable_order",
        selection_order=["executable_authorized","required_inputs_available_or_executable_plan",
                         "available_before_planned","comparable_suspected_source_reachability",
                         "declared_work_proxy" if design["cost_mode"] == "declared_work" else "empirical_estimate",
                         "stable_keep_then_plan_id"],
        risk=chosen["risk"],
        risk_comparison_scope="registered_sources_and_targets_only_unknown_semantic_effects",
        alternatives=[{key:value for key,value in row.items() if key != "plan"} for row in rows],
        rewrite_plan=asdict(chosen["plan"]))
    controller.trace.log("rewrite_selection_cost",selection_wall_seconds=time.monotonic()-started,
                         model_cost_accounting="shared_episode_usage_by_purpose")
    controller.last_joint_task_revision = controller.task_revision
    controller.last_joint_risk_event_count = len(controller.framework_team.risk_events)
    controller.last_cost_reconsidered_chars = controller.observed_repeated_object_chars
    return chosen["plan"]
