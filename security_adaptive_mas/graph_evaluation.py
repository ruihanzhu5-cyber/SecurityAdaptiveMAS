"""AND input coverage and conservative live-object reachability, not ASR."""
from __future__ import annotations

from collections import deque

from .online_task import task_need_snapshot


def plan_need_status(controller, runtime, plan, graph=None):
    """AND over needs, OR over explicitly checked providers; planned is not released."""
    rows = []
    graph = runtime.graph if graph is None else graph
    descriptions = {task.task_id:task.description for task in controller.task_analysis.subtasks}
    for need in controller.need_specs.values():
        readable = []
        alternatives = []
        for provider in need.provider_alternatives:
            key = (provider["task_id"],provider["output_slot"])
            object_id = runtime.bindings.get(key)
            if object_id is not None:
                obj = runtime.objects[object_id]
                if obj["producer_task_description"] != descriptions[provider["task_id"]]:
                    continue
                check = runtime.checks.get(object_id)
                fields = need.acceptance_contract["required_fields"]
                independent = all(not controller.provenance.derived_from(object_id, root)
                                  for root in need.acceptance_contract["independent_from"])
                if check and check["status"] == "passed" and independent and set(fields) <= set(obj["fields"] or {}):
                    alternatives.append(object_id)
                    consumer = dict(plan.assignments).get(need.consumer_task_id,"agent_0")
                    if (consumer,object_id) in graph.object_access or (consumer,object_id) in plan.existing_object_deliveries:
                        readable.append(object_id)
        remaining_tasks = {a.task_id for a in plan.activations}
        consumer = dict(plan.assignments).get(need.consumer_task_id,"agent_0")
        owners = dict(plan.assignments)
        planned = any(binding["need_id"] == need.need_id and binding["producer_task"] in remaining_tasks
                      and (owners[binding["producer_task"]] == consumer or
                           (owners[binding["producer_task"]],consumer) in graph.extra_edges)
                      for binding in plan.future_output_bindings)
        status = "satisfied" if readable else "planned" if planned else "unsatisfied" if need.provider_alternatives else "unknown"
        rows.append({"need_id":need.need_id,"consumer_task_id":need.consumer_task_id,
                     "status":status,"status_evidence_ids":readable,"qualified_alternatives":alternatives})
    return rows


def temporal_plan_risk(runtime, plan, graph, protected_targets):
    """Causal historical events plus a distinct future projection, not attack success."""
    adjacency = {}
    def add(a,b):
        adjacency.setdefault(a,set()).add(b)
    for object_id,obj in runtime.objects.items():
        for parent in obj["parent_ids"]:
            if parent in runtime.objects and runtime.objects[parent]["created_sequence"] <= obj["created_sequence"]:
                add(parent,object_id)
    for instance,object_ids in runtime.exposures.items():
        for object_id in object_ids:
            add(object_id,"state:"+instance)
    for instance,object_id in graph.object_access:
        add(object_id,"state:"+instance)
    # Future states cannot point to past artifacts. All generated outputs have new nodes.
    for index,activation in enumerate(plan.activations):
        node = "future:"+activation.activation_id
        add("state:"+activation.instance_id,node)
        role = dict(plan.role_specs)[activation.instance_id]
        for source_id in role.source_evidence_ids:
            add(source_id,node)  # a declared derivation of role instructions, never an authorization
        output = node+":output"
        add(node,output)
        for tool in graph.agent(activation.instance_id).allowed_tools:
            add(node,"tool:"+tool)
        for source,target in graph.extra_edges:
            if source == activation.instance_id:
                if target == "agent_0":
                    add(output,"future:coordinator_continuation")
                for later in plan.activations[index+1:]:
                    if later.instance_id == target:
                        add(output,"future:"+later.activation_id)
    add("state:agent_0","future:coordinator_continuation")
    for tool in graph.agent("agent_0").allowed_tools:
        add("future:coordinator_continuation","tool:"+tool)
    external = {key for key,obj in runtime.objects.items() if obj["low_trust"]
                and runtime.controller.provenance.items[key].source != "startup"}
    team = runtime.controller.framework_team
    suspected = {key for event in team.risk_events if event["suspicion"] in {"supported","flagged"}
                 for key in (event["object_ids"] if "object_ids" in event else (event["object_id"],))}
    def reach(roots):
        queue = deque(sorted(roots)); paths = {r:[r] for r in roots}
        while queue:
            node = queue.popleft()
            for target in sorted(adjacency.get(node,())):
                if target not in paths:
                    paths[target] = paths[node]+[target]; queue.append(target)
        return {target:paths[target] for target in protected_targets if target in paths}
    history = [dict(row) for row in runtime.flow_events]
    return {"risk_model":"activation_ordered_history_and_future_projection",
            "observed_information_flow":history,"projected_future_flow":reach(suspected),
            "external_source_reachability":reach(external),"suspected_roots":sorted(suspected),
            "semantic_adoption":"unknown","state_effect":"unknown",
            "instruction_source_semantics":"declared_derivation_not_raw_text_exposure",
            "scope_gaps":["shared_application_state_not_bound_to_versioned_objects",
                          "model_internal_semantic_causality_not_observed"]}


def coverage(analysis, graph, objects):
    rows = [{"task_id": need.task_id, "requirement_id": need.need_id,
             "status": need.status, "owner_instance_id": need.owner_instance_id,
             "status_evidence_ids": need.status_evidence_ids}
            for need in task_need_snapshot(analysis, graph, objects)]
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
