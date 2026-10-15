"""A single policy/MANTA/ACL boundary for live and stored artifact delivery."""
from dataclasses import asdict
import json
from uuid import uuid4
from ..security import Message
from .contracts import ReconfigurationCandidate


class DeliveryService:
    def __init__(self, session):
        self.s=session
        self.packet_counter=0

    def deliver_authorized(self, consumer, ref, decision_id, stamp):
        s=self.s
        if stamp!=s.runtime.stamp():raise ValueError("stale_delivery_stamp")
        transport=s.runtime.check_delivery(consumer,ref)
        author=s.runtime.objects[ref.key]["writer"]
        evidence=s.provenance.items[ref.object_id]
        self.packet_counter+=1
        packet={"message_id":f"packet_{self.packet_counter:06d}","dispatch_id":self.packet_counter,
            "sender":author,"recipients":[consumer],"kind":"artifact","phase":"delivery",
            "round":s.observation_recorder.snapshot().sequence,"discussion_index":0,
            "artifact_id":ref.object_id,"content":evidence.text,
            "payload":{"artifact_id":ref.object_id,"summary":evidence.text}}
        s.manta.sidecars[packet["message_id"]]={"ref":ref,"graph_version":s.runtime.graph.version,
            "original_author":author,"view_id":ref.view_id,"decision_id":decision_id}
        selected=s.manta.select_packets(s.runtime.graph,[packet],consumer_id=consumer)
        if [p["message_id"] for p in selected]!=[packet["message_id"]]:
            raise RuntimeError("MANTA_packet_not_selected")
        if selected[0]["content"]!=evidence.text:
            raise RuntimeError("MANTA_content_does_not_match_evidence")
        if transport=="persistent_store":
            s.runtime.authorize_stored_delivery(consumer,ref,stamp)
        elif author!=consumer:
            s.message_gateway.send(s.runtime.graph,Message(author,consumer,s.runtime.graph.version,ref.object_id,evidence.text))
            delivered=s.message_gateway.deliver(s.runtime.graph)
            if len(delivered)!=1 or delivered[0].evidence_id!=ref.object_id:
                raise RuntimeError("live_message_not_delivered")
        s.runtime.check_delivery(consumer,ref)
        s.seen.setdefault(consumer,set()).add(ref.key)
        s.runtime.receipts.add((consumer,ref.key))
        s.trace.log("runtime_object_delivered",agent_id=consumer,object_id=ref.object_id,object_version=ref.version,
            view_id=ref.view_id,author_instance_id=author,transport=transport,decision_id=decision_id,
            graph_version=s.runtime.graph.version,delivery_bytes=len(evidence.text.encode()))
        return selected[0]["content"]

    def protected(self, consumer, graph):
        return bool(self.protected_paths(consumer,graph))

    def protected_paths(self,consumer,graph):
        s=self.s
        visited=set()
        frontier=[(consumer,[consumer])]
        paths=[]
        while frontier:
            node,path=frontier.pop(0)
            if node in visited:continue
            visited.add(node)
            tools=[t for t in graph.agent(node).allowed_tools if s.tool_gateway.tools[t].mode=="write"]
            if tools:paths.append({"agents":path,"write_tools":sorted(tools)})
            frontier.extend((b,path+[b]) for a,b in sorted(graph.extra_edges) if a==node and b not in visited)
        return paths

    def source_suspect(self, ref):
        s=self.s
        return any(r.get("suspicion")=="supported" and s.provenance.derived_from(ref.object_id,
            key.split("@")[0]) for key,r in s.risk_states.items())

    def request(self, request):
        s=self.s
        record=s.artifact_records[request.artifact_ref.key]
        ref=record.ref
        s.trace.log("delivery_requested",**asdict(request),current_stamp=asdict(s.runtime.stamp()))
        # Assess the actual result (or actual typed body atoms), once per goal revision.
        roots=record.parent_refs if record.payload_kind=="email_metadata_v1" else (ref,)
        source_ref=roots[0] if roots else ref
        ids=s.body_atoms.get(source_ref.key,(source_ref.object_id,))
        risk=s.risk_observer.assess(record.producer_id,tuple(ids)) if ids else {"suspicion":"not_supported","reason_evidence_ids":[]}
        s.risk_states[source_ref.key]=risk
        if record.payload_kind!="email_metadata_v1" and self.source_suspect(ref):
            risk={**risk,"suspicion":"supported"}
        stamp=s.runtime.stamp()
        candidates=[]
        def add(action, selected_ref=None, work=None, call=None):
            selected_record=s.artifact_records[selected_ref.key] if selected_ref else None
            ops=[]
            if selected_ref:
                author=selected_record.producer_id
                active={a.agent_id for a in s.runtime.graph.agents}
                if author in active and author!=request.requester_id:
                    ops.append({"op":"ADD_ROUTE","source":author,"target":request.requester_id})
                ops.append({"op":"SET_OBJECT_ACCESS","agent_id":request.requester_id,"object_id":selected_ref.key,"allowed":True})
            replacement=None
            instruction_record=None
            if action=="REASSIGN":
                replacement=f"worker_{s.worker_counter+1:04d}"
                instruction_record=s.artifact_records[type(ref)(work.instruction_evidence_id).key]
                ops.extend([{"op":"SPAWN_WORKER","agent_id":replacement,"tools":work.requested_tools},
                    {"op":"ASSIGN","task_id":work.work_id,"agent_id":replacement},
                    {"op":"ADD_ROUTE","source":work.requester_id,"target":replacement},
                    {"op":"SET_OBJECT_ACCESS","agent_id":replacement,"object_id":instruction_record.ref.key,"allowed":True}])
            candidate=ReconfigurationCandidate(f"{request.request_id}:{action}",action,stamp,tuple(ops),
                {selected_ref.key:request.requester_id} if selected_ref else {ref.key:"_quarantine"},
                work.work_id if work else None,(selected_ref,) if selected_ref else (),
                (selected_ref.object_id,) if selected_ref else (ref.object_id,),
                {"model_steps_upper_bound":2*s.config["worker_max_steps"] if action=="REASSIGN" else 1 if action=="REQUERY" else 0,
                 "delivery_bytes":len(s.provenance.items[selected_ref.object_id].text.encode()) if selected_ref else 0,
                 "new_instance":action=="REASSIGN"},
                {"suspicion":risk["suspicion"],"source_lineage_reachability":s.provenance.contains_low_trust(ref.object_id),
                 "carries_suspect_payload":not(selected_record and selected_record.payload_kind=="email_metadata_v1"),
                 "payload_data_dependencies":selected_record.data_dependencies if selected_record else (),
                 "semantic_adoption":"unknown","preexisting_exposure":ref.object_id in s.runtime.exposures.get(request.requester_id,set())})
            if call:candidate.cost_proxy["replay_call_id"]=call
            if replacement:
                candidate.cost_proxy["replacement_instance"]=replacement
                candidate.object_dispositions[instruction_record.ref.key]=replacement
            try:
                patch=s.patch(ops or [{"op":"KEEP"}],candidate.evidence_ids,action,stamp)
                graph=s.runtime.preview(patch)
                paths=self.protected_paths(request.requester_id,graph)
                candidate.risk_projection["protected_receiver"]=bool(paths)
                candidate.risk_projection["payload_reachability"]={"ref":selected_ref.key if selected_ref else None,
                    "protected_paths":paths if selected_ref else [],"future_result":"unknown" if work else None}
                candidate.risk_projection["source_lineage_reachability"]={
                    "contains_external_source":s.provenance.contains_low_trust(ref.object_id),
                    "parent_refs":[r.key for r in record.parent_refs],"reason_evidence_ids":risk["reason_evidence_ids"]}
                candidate.risk_projection["observed_submissions"]=[row for row in s.runtime.flow_events
                    if row["flow_kind"]=="model_submitted" and any(s.provenance.derived_from(key,source_ref.object_id) for key in row["object_ids"])]
                affected=[selected_record] if selected_record else [record]
                if instruction_record:affected.append(instruction_record)
                parent_refs={r.key for values in s.handles.get("agent_0",{}).values() for r in values.values()}
                # Transfer eligibility follows a runtime-issued handle to this exact request object.
                parent_refs.update(r.ref.key for r in affected if r.ref.key in {v.key for values in s.handles.get(request.requester_id,{}).values() for v in values.values()})
                candidate.validation=s.validate_mutation(graph,affected,candidate.object_dispositions,parent_refs=parent_refs)
                candidate.status="validated"
            except (ValueError,PermissionError) as error:
                candidate.status="rejected"
                candidate.rejection_reason=str(error)
            candidates.append(candidate)
        fields=set(request.required_fields)
        if not fields or "full" in record.fields or fields<=set(record.fields):add("RELEASE",ref)
        projected=s.views.get((ref.key,"email_metadata_v1"))
        if projected is not None and fields and fields<=set(s.artifact_records[projected.key].fields):
            if projected==s.resolve(request.requester_id,ref.object_id,"email_metadata_v1"):
                add("LIMITED_RELEASE",projected)
        add("HOLD")
        work=s.work_items.get(record.producer_work_id)
        if work and work.repair_attempts<s.config["max_auto_repairs_per_work"] and self.repair_budget_available():
            calls=[key for key,c in s.calls.items() if c["work_id"]==work.work_id and "result_ref" in c
                   and s.tool_gateway.tools[c["tool"]].mode=="read"]
            if calls and work.producer_id in {a.agent_id for a in s.runtime.graph.agents}:add("REQUERY",work=work,call=calls[-1])
            bootstrap_suspect=self.source_suspect(type(ref)(work.instruction_evidence_id))
            if (not bootstrap_suspect and s.budget.max_requests-s.budget.requests>=2*s.config["worker_max_steps"]
                and len(s.runtime.instances)<s.config["max_total_instances"] and len(s.runtime.graph.agents)<s.config["max_active_agents"]):
                add("REASSIGN",work=work)
            elif bootstrap_suspect:
                s.trace.log("unsupported",module="delivery",reason="bootstrap_source_risk",work_id=work.work_id)
        if len(candidates)>s.config["max_candidates"]:raise ValueError("candidate_limit")
        selected=s.policy.select(candidates)
        s.trace.log("candidates_evaluated",request_id=request.request_id,method=s.policy.method,
                    candidates=[c.as_dict() for c in candidates],selected=selected.candidate_id)
        if stamp!=s.runtime.stamp():raise ValueError("stale_selected_candidate")
        patch=s.patch(selected.graph_operations or [{"op":"KEEP"}],selected.evidence_ids,selected.action,stamp)
        s.runtime.apply(patch)
        s.trace.log("reconfiguration_selected",request_id=request.request_id,action=selected.action,
            decision_id=selected.candidate_id,graph_version=s.runtime.graph.version,object_epoch=s.runtime.object_epoch,
            consumed_atm_destinations=selected.validation["destinations"])
        if selected.action=="HOLD":
            s.last_return_refs=[]
            s.trace.log("delivery_held",request_id=request.request_id,work_id=request.for_work_id,ref=asdict(ref))
            return {"status":"held","request_id":request.request_id,"artifact_id":ref.object_id,"semantic_verified":"unknown"}
        if selected.action in {"REQUERY","REASSIGN"}:
            work.repair_attempts+=1
            s.trace.log("repair_started",work_id=work.work_id,action=selected.action,same_source=True,
                        prior_instance=work.producer_id,old_result_ref=asdict(ref))
            if selected.action=="REQUERY":
                call=s.calls[selected.cost_proxy["replay_call_id"]]
                _,new=s.invoke_tool(work.producer_id,work.work_id,call["tool"],call["arguments"])
                work.result_refs.append(new)
                receipt={"work_id":work.work_id,"status":"repairing","artifact_handles":s.handle_receipt(request.requester_id,[new])}
            else:
                receipt=s.run_work(work,repair=True,prepared_worker=selected.cost_proxy["replacement_instance"])
            s.last_return_refs=[]
            s.trace.log("repair_finished",work_id=work.work_id,action=selected.action,same_source=True,
                        replacement_instance=work.producer_id,future_delivery="requires_new_request_and_review")
            return {**receipt,"work_status":receipt["status"],"status":"repairing","request_id":request.request_id}
        chosen=selected.delivery_refs[0]
        text=self.deliver_authorized(request.requester_id,chosen,selected.candidate_id,s.runtime.stamp())
        s.grant_handle(request.requester_id,chosen)
        s.last_return_refs=[chosen]
        return {"status":"limited" if selected.action=="LIMITED_RELEASE" else "released",
            "request_id":request.request_id,"artifact_ref":asdict(chosen),"content":text,"semantic_verified":"unknown"}

    def repair_budget_available(self):
        b=self.s.budget
        return b.requests<b.max_requests and b.peak_cost_estimate_usd+b.unknown_cost_bound<b.max_cost_usd
