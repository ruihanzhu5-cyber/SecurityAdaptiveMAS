"""Per-episode services. No global task planner or topology enumeration."""
from dataclasses import asdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..security import ProvenanceStore, ToolGateway, MessageGateway, ToolDefinition, SandboxPermissivePolicy
from ..online_observation import OnlineObservationRecorder
from ..graph_runtime import GraphRuntime, TopologyPatch
from ..risk_monitor import RiskMonitor
from ..adapters.atm_mutation import ATMMutationAdapter
from ..adapters.manta_context import MantaContextAdapter
from .contracts import ArtifactRef, ArtifactRecord, WorkItem, string_list
from .policy import ReconfigurationPolicy


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


class ModularSession:
    def __init__(self, config, trace, client):
        self.config,self.trace,self.client=config,trace,client
        self.budget=client.budget
        self.provenance=ProvenanceStore(trace)
        self.observation_recorder=OnlineObservationRecorder()
        self.goal_revision=0
        self.tool_gateway=ToolGateway(self.provenance,trace,frozenset(),frozenset())
        self.message_gateway=MessageGateway(self.provenance,trace)
        self.work_items,self.artifact_records,self.typed_snapshots={}, {}, {}
        self.handles,self.seen,self.views={}, {}, {}
        self.risk_states,self.body_atoms,self.atom_fields={}, {}, {}
        self.input_components,self.last_submitted,self.rendered_components={}, {}, []
        self.current_evidence,self.calls,self.last_return_refs={}, {}, []
        self.runtime=None
        self.attack_runtime=None
        self.stop_callback=None
        self.terminal_error=None
        self.worker_steps=0
        self.turns=0
        self.coordinator_prepared_requests=0
        self.time_manager=None
        self.simulation_started_at=None
        self.call_counter=0
        self.worker_counter=0
        self.rendering=False
        self.render_excluded=set()
        self.render_roles=set()
        self.pending_call_id=None
        self.call_returned_refs={}
        self.bound_logs={}
        self._controlled_starting=False
        self.handoff_pilot=None
        self.evidence_builder=None
        if config.get('version')=='v0.7':
            from .evidence_views import EvidenceViewBuilder
            from .handoff_pilot import requirements
            experiment=config['experiment']
            self.evidence_builder=EvidenceViewBuilder(self,requirements(experiment.get('requirements_path'))[experiment['case_id']]['required_fields'])
        if config.get('version')=='v0.7' and config['experiment']['mode']=='controlled_handoff':
            from .handoff_pilot import ControlledHandoffPilot
            self.handoff_pilot=ControlledHandoffPilot(self,config['experiment'])
        self.policy=ReconfigurationPolicy(config["method"])
        lock=json.loads(Path(config["upstream_lock"]).read_text(encoding="utf-8"))
        self.atm=ATMMutationAdapter(config["paths"]["atm_root"],lock,trace)
        self.risk_observer=RiskMonitor(client,self.provenance,self.goal_text,trace,"semantic")
        self.startup=self.provenance.add("startup","low_trust","Waiting for delivered USER/ENV messages")
        self.current_evidence["agent_0"]=self.startup.evidence_id

    def goal_text(self):
        return self.observation_recorder.snapshot().task_description or ""

    def configure_tools(self, originals):
        self.original_tools=originals
        names=frozenset(originals)
        reads=frozenset(k for k,v in originals.items() if v.app_tool.write_operation is False)
        writes=names-reads
        self.tool_gateway.authorized_tools=names
        self.tool_gateway.authorized_resources=frozenset(t.app_tool.app_name for t in originals.values())
        self.tool_gateway.authorization=SandboxPermissivePolicy(self.provenance,self.trace,allowed_write_tools=writes)
        for name,tool in originals.items():
            self.tool_gateway.register(ToolDefinition(name,"read" if name in reads else "write",frozenset({tool.app_tool.app_name}),tool))
        self.runtime=GraphRuntime(trace=self.trace,provenance=self.provenance,tool_gateway=self.tool_gateway,
            message_gateway=self.message_gateway,coordinator_tools=names,worker_tools=reads,
            max_agents=self.config["max_active_agents"],max_total_instances=self.config["max_total_instances"],
            reconfiguration_limit=self.config["max_reconfigurations"],
            observation_stamp=lambda:(self.observation_recorder.snapshot().sequence,self.goal_revision))
        self.manta=MantaContextAdapter(self.runtime,self.trace)
        from .delivery import DeliveryService
        self.delivery_service=DeliveryService(self)
        self.trace.log("modular_tools_registered",coordinator_tools=sorted(names),worker_tools=sorted(reads),
                       protected_tools=sorted(writes),source="real_app_tool_metadata")

    def on_notification(self, kind, text, timestamp=None):
        evidence=self.provenance.add("are:"+kind,"trusted_task" if kind=="user" else "low_trust",text)
        self.observation_recorder.record_notification(kind,text,evidence.evidence_id,agent_instance_id="agent_0",simulation_time=timestamp)
        if kind=="user":
            self.goal_revision+=1
            self.tool_gateway.authorization.register_user(evidence.evidence_id)
        ref=self.register_artifact(evidence,"agent_0","root",payload_kind="notification",fields=("text",))
        self.grant_handle("agent_0",ref)
        self.seen.setdefault("agent_0",set()).add(ref.key)
        self.current_evidence["agent_0"]=evidence.evidence_id
        return ref

    def register_artifact(self, evidence, producer, work_id, *, payload_kind, parents=(), fields=("result",),
                          view_id="full", call_id=None, tool=None, dependencies=(), lineage=()):
        ref=ArtifactRef(evidence.evidence_id,1,view_id)
        record=ArtifactRecord(ref,producer,work_id,call_id,tool,payload_kind,tuple(parents),
            len(self.artifact_records)+1,(view_id,),tuple(fields),tuple(dependencies),tuple(lineage))
        self.artifact_records[ref.key]=record
        self.runtime.add_object(ref,producer,task_id=work_id)
        self.trace.log("artifact_registered",**asdict(record))
        return ref

    def grant_handle(self, consumer, ref):
        self.handles.setdefault(consumer,{}).setdefault(ref.object_id,{})[ref.view_id]=ref

    def resolve(self, consumer, object_id, view):
        try:return self.handles[consumer][object_id][view]
        except KeyError:raise PermissionError("unknown_or_ungranted_artifact_handle") from None

    def handle_receipt(self, consumer, refs):
        result=[]
        for ref in refs:
            self.grant_handle(consumer,ref)
            available=[ref.view_id]
            if (ref.key,"email_metadata_v1") in self.views:
                projected=self.views[(ref.key,"email_metadata_v1")]
                self.handles[consumer][ref.object_id]["email_metadata_v1"]=projected
                available.append("email_metadata_v1")
            result.append({"object_id":ref.object_id,"version":ref.version,"views":available})
            if (ref.key,'task_evidence_v1') in self.views:
                projected=self.views[(ref.key,'task_evidence_v1')]
                self.handles[consumer][ref.object_id]['task_evidence_v1']=projected
                available.append('task_evidence_v1')
        return result

    def component(self, ref, kind="artifact", **metadata):
        text=self.provenance.items[ref.object_id].text
        return {"kind":kind,**asdict(ref),"rendered_payload_hash":text_hash(text),**metadata}

    def register_tool_result(self, call_id, caller_id, work_id, typed_result, evidence):
        from are.simulation.apps.email_client import ReturnedEmails
        snapshot=deepcopy(typed_result)
        full=self.register_artifact(evidence,caller_id,work_id,payload_kind=type(snapshot).__name__,
            call_id=call_id,tool=self.calls[call_id]["tool"],fields=("full",))
        self.typed_snapshots[full.key]=snapshot
        assess_ids=[full.object_id]
        if isinstance(snapshot,ReturnedEmails):
            assess_ids=[]
            bodies=[]
            for index,email in enumerate(snapshot.emails):
                for field_name,value in (("content",email.content),("email_id",email.email_id),("timestamp",email.timestamp)):
                    field_path=f"emails[{index}].{field_name}"
                    atom=self.provenance.add("typed_field:"+field_path,"derived",json.dumps(value,ensure_ascii=False),
                        parents=(full.object_id,),owner_agent=caller_id)
                    atom_ref=self.register_artifact(atom,caller_id,work_id,payload_kind="field_atom",parents=(full,),
                        fields=(field_path,),call_id=call_id,tool=self.calls[call_id]["tool"],dependencies=(field_path,),lineage=(full.key,))
                    self.atom_fields[atom_ref.object_id]=field_path
                    if field_name=="content":
                        bodies.append(atom_ref.object_id)
                        assess_ids.append(atom_ref.object_id)
            page={"emails":[{"email_id":e.email_id,"timestamp":e.timestamp} for e in snapshot.emails],
                  "emails_range":list(snapshot.emails_range),"total_returned_emails":snapshot.total_returned_emails,
                  "total_emails":snapshot.total_emails}
            paths=tuple(f"emails[{i}].{f}" for i in range(len(snapshot.emails)) for f in ("email_id","timestamp"))+("emails_range","total_returned_emails","total_emails")
            projection=self.provenance.add("email_metadata_v1","derived",json.dumps(page,ensure_ascii=False),
                parents=(full.object_id,),owner_agent=caller_id)
            view=self.register_artifact(projection,caller_id,work_id,payload_kind="email_metadata_v1",parents=(full,),
                fields=("email_id","timestamp","emails_range","total_returned_emails","total_emails"),
                view_id="email_metadata_v1",call_id=call_id,tool=self.calls[call_id]["tool"],dependencies=paths,lineage=(full.key,))
            self.views[(full.key,"email_metadata_v1")]=view
            self.body_atoms[full.key]=tuple(bodies)
            # Preserve page fields as separately identified atoms, without using them as trusted goals.
            for name in ("emails_range","total_returned_emails","total_emails"):
                atom=self.provenance.add("typed_field:"+name,"derived",json.dumps(page[name]),parents=(full.object_id,),owner_agent=caller_id)
                self.register_artifact(atom,caller_id,work_id,payload_kind="page_atom",parents=(full,),fields=(name,),
                    call_id=call_id,dependencies=(name,),lineage=(full.key,))
        if assess_ids:
            risk=self.risk_observer.assess(caller_id,tuple(assess_ids))
            self.risk_states[full.key]=risk
            for key in risk["reason_evidence_ids"]:self.risk_states[key]=risk
        else:
            self.risk_states[full.key]={"suspicion":"not_supported","reason_evidence_ids":[],"scope":"empty_returned_page"}
        self.grant_handle(caller_id,full)
        self.seen.setdefault(caller_id,set()).add(full.key)
        if self.evidence_builder is not None:
            self.evidence_builder.ensure(full)
        return full

    def invoke_tool(self, caller, work_id, name, arguments):
        if name not in self.original_tools:raise PermissionError("unknown_application_tool")
        self.call_counter+=1
        call_id=f"call_{self.call_counter:06d}"
        self.calls[call_id]={"caller":caller,"work_id":work_id,"tool":name,"arguments":deepcopy(arguments)}
        original=self.original_tools[name]
        application=context=None
        def transform(value,*,tool_name,agent_id):
            nonlocal application,context
            from ..attacks.contracts import AttackContext
            context=AttackContext(self.attack_runtime.scenario_id,agent_id,tool_name,call_id,'tool_result',value)
            application=self.attack_runtime.apply(context)
            return application.modified_result
        result,evidence=self.tool_gateway.call(self.runtime.graph,caller,self.runtime.graph.version,name,
            original.app_tool.app_name,arguments,self.current_evidence[caller],
            result_transform=transform if self.attack_runtime is not None and original.app_tool.write_operation is False else None)
        if application is not None:
            self.attack_runtime.bind_delivery(application,evidence_id=evidence.evidence_id,context=context)
        self.observation_recorder.record_action(name,self.current_evidence[caller],agent_instance_id=caller,call_id=call_id,status="completed")
        self.observation_recorder.record_tool_result(name,evidence.evidence_id,agent_instance_id=caller,call_id=call_id,status="completed")
        ref=self.register_tool_result(call_id,caller,work_id,result,evidence)
        self.calls[call_id]["result_ref"]=ref
        self.current_evidence[caller]=evidence.evidence_id
        self.last_return_refs=[ref]
        return result,ref

    def patch(self, operations, evidence_ids=(), reason="", stamp=None):
        stamp=stamp or self.runtime.stamp()
        return TopologyPatch(uuid4().hex,stamp.graph_version,stamp.observation_sequence,stamp.goal_revision,
            tuple(operations),reason=reason,evidence_ids=tuple(evidence_ids),base_object_epoch=stamp.object_epoch)

    def validate_mutation(self, graph, records, destinations, parent_id="agent_0", parent_refs=None):
        parent=self.runtime.graph.agent(parent_id)
        if parent_refs is None:
            parent_refs={ref.key for values in self.handles.get(parent_id,{}).values() for ref in values.values()}
        children=[{"id":a.agent_id,"tools":list(a.allowed_tools),"memory_scope":[key for key,dst in destinations.items() if dst==a.agent_id],"forbidden_tools":[]} for a in graph.agents]
        return self.atm.validate(parent_id=parent_id,parent_tools=parent.allowed_tools,parent_refs=parent_refs,
            denied_tools=set(),children=children,records=records,destinations=destinations,provenance=self.provenance)

    def request_delegation(self, caller_id, request):
        if caller_id!="agent_0":raise PermissionError("nested_delegation_not_supported")
        tools=string_list(request["tools"],"tools")
        input_ids=string_list(request["context_artifact_ids"],"context_artifact_ids")
        fields=string_list(request["output_fields"],"output_fields")
        if request["model"]!="deepseek-flash" or not isinstance(request["task_instruction"],str) or not request["task_instruction"].strip():
            raise ValueError("invalid_delegation_model_or_instruction")
        if not set(tools)<=self.runtime.worker_tools:raise PermissionError("worker_tools_must_be_readonly_subset")
        if not self.goal_text():raise PermissionError("delegation_requires_arrived_USER")
        inputs=[]
        for key in input_ids:
            if key not in self.handles.get(caller_id,{}):raise PermissionError("unseen_delegation_input")
            values=self.handles[caller_id][key]
            inputs.extend(values.values())
        parents=tuple(dict.fromkeys([e.evidence_id for e in self.observation_recorder.snapshot().user_events]+
            [part["object_id"] for part in self.last_submitted.get(caller_id,[]) if "object_id" in part]))
        if self._controlled_starting:
            parents=tuple(dict.fromkeys([*parents,*[r.object_id for r in inputs]]))
        instruction=self.provenance.add("coordinator:delegation","derived",request["task_instruction"],parents=parents,owner_agent=caller_id)
        work_id=f"work_{len(self.work_items)+1:04d}"
        work=WorkItem(work_id,caller_id,self.goal_revision,instruction.evidence_id,tuple(dict.fromkeys(inputs)),tools,fields)
        self.work_items[work_id]=work
        self.trace.log("delegation_requested",work_id=work_id,requester=caller_id,instruction_evidence_id=instruction.evidence_id,
            input_refs=[asdict(r) for r in work.input_refs],exact_tools=tools,unknown_sources="official_rendering_unknown",
            initiation_mode='controlled_handoff' if self._controlled_starting else 'autonomous')
        instruction_ref=self.register_artifact(instruction,caller_id,work_id,payload_kind="work_instruction",fields=("instruction",))
        self.grant_handle(caller_id,instruction_ref)
        return self.run_work(work,instruction_ref=instruction_ref)

    def start_controlled_work(self,plan):
        from .delegation import DelegateTaskTool
        snapshot=self.observation_recorder.snapshot()
        if self.handoff_pilot is None or plan.goal_revision!=self.goal_revision:
            raise ValueError('controlled_handoff_disabled_or_stale_goal')
        if not snapshot.user_events or not {r.object_id for r in plan.source_refs}<=set(snapshot.evidence_ids):
            raise PermissionError('controlled_handoff_requires_arrived_evidence')
        self._controlled_starting=True
        try:
            return DelegateTaskTool(self).forward(plan.work_instruction,[r.object_id for r in plan.source_refs],
                list(plan.tools),list(plan.required_fields),'deepseek-flash')
        finally:
            self._controlled_starting=False

    def run_work(self, work, *, instruction_ref=None, repair=False, prepared_worker=None):
        from .worker_runner import run_sync
        self.worker_counter+=1
        worker=f"worker_{self.worker_counter:04d}"
        if prepared_worker is not None and prepared_worker!=worker:
            raise ValueError("prepared_worker_identity_mismatch")
        if instruction_ref is None:instruction_ref=ArtifactRef(work.instruction_evidence_id)
        operations=[{"op":"SPAWN_WORKER","agent_id":worker,"tools":work.requested_tools},
            {"op":"ASSIGN","task_id":work.work_id,"agent_id":worker},
            {"op":"ADD_ROUTE","source":work.requester_id,"target":worker},
            {"op":"SET_OBJECT_ACCESS","agent_id":worker,"object_id":instruction_ref.key,"allowed":True}]
        if prepared_worker is None and len(self.runtime.graph.agents)>=self.config["max_active_agents"]:
            eligible=[a.agent_id for a in self.runtime.graph.agents if a.agent_id!="agent_0" and a.lifecycle_status=="idle"
                and not any(w.status=="running" and w.producer_id==a.agent_id for w in self.work_items.values())
                and any(consumer!=a.agent_id and self.runtime.objects[key]["writer"]==a.agent_id
                        for consumer,key in self.runtime.receipts)]
            if not eligible:raise RuntimeError("active_capacity_no_idle_delivered_worker")
            operations.insert(0,{"op":"RETIRE_WORKER","agent_id":sorted(eligible)[0]})
        if prepared_worker is None:
            from .contracts import ReconfigurationCandidate
            patch=self.patch(operations,(instruction_ref.object_id,),"concrete delegation")
            graph=self.runtime.preview(patch)
            validation=self.validate_mutation(graph,[self.artifact_records[instruction_ref.key]],{instruction_ref.key:worker})
            if validation["destinations"]!={instruction_ref.key:worker}:raise ValueError("ATM_submission_destination_mismatch")
            candidate=ReconfigurationCandidate(work.work_id+":DELEGATE","DELEGATE",self.runtime.stamp(),tuple(operations),
                {instruction_ref.key:worker},work.work_id,evidence_ids=(instruction_ref.object_id,),
                cost_proxy={"model_steps_upper_bound":2*self.config["worker_max_steps"],"new_instance":True},
                status="validated",validation=validation)
            selected=self.policy.select([candidate])
            self.trace.log("candidates_evaluated",work_id=work.work_id,method=self.policy.method,
                candidates=[candidate.as_dict()],selected=selected.candidate_id)
            self.runtime.apply(patch)
        elif self.runtime.graph.assignments.get(work.work_id)!=worker:
            raise ValueError("prepared_assignment_missing")
        work.producer_id,work.status=worker,"running"
        for ref in work.input_refs:self.grant_handle(worker,ref)
        self.grant_handle(worker,instruction_ref)
        # Instruction is an explicitly authorized delivery, not a global evidence digest.
        self.seen[worker]=set()
        self.delivery_service.deliver_authorized(worker,instruction_ref,"delegation",self.runtime.stamp())
        self.current_evidence[worker]=work.instruction_evidence_id
        self.trace.log("worker_started",instance_id=worker,work_id=work.work_id,repair=repair,
            source_fork_version="14a1a2051d6b03c479b706f8f555a60b8419e3b5")
        try:
            refs,steps=run_sync(self,work)
        except Exception:
            work.status="failed"
            raise
        self.runtime.graph.agent(worker).lifecycle_status="idle"
        work.result_refs=list(refs)
        if work.status=="running":work.status="artifact_ready" if refs else "partial"
        self.trace.log("worker_finished",instance_id=worker,work_id=work.work_id,local_status=work.status,
            actual_usage=self.budget.actual_usage_by_purpose.get("worker:"+worker),steps=steps)
        return {"work_id":work.work_id,"status":work.status,
            "artifact_handles":self.handle_receipt(work.requester_id,refs),"steps":steps,"semantic_verified":"unknown"}

    def request_delivery(self, caller_id, request):
        from .contracts import DeliveryRequest
        required=string_list(request["required_fields"],"required_fields")
        work_id=request["for_work_id"]
        if (caller_id=="agent_0" and work_id!="root") or (caller_id!="agent_0" and self.runtime.graph.assignments.get(work_id)!=caller_id):
            raise PermissionError("delivery_work_identity_mismatch")
        ref=self.resolve(caller_id,request["artifact_id"],request["view"])
        delivery=DeliveryRequest(uuid4().hex,caller_id,work_id,ref,required,self.runtime.graph.version)
        return self.delivery_service.request(delivery)

    def worker_input_components(self, worker_id, prompt):
        return self.input_components[worker_id]

    def record_submission(self, instance_id, work_id, components, request_id):
        self.last_submitted[instance_id]=deepcopy(components)
        ids=[p["object_id"] for p in components if "object_id" in p]
        self.runtime.record_submission(instance_id,work_id,ids,request_id)

    def before_coordinator_input(self, messages):
        owned=[p for p in self.rendered_components if p.get("kind")!="official_rendering_unknown"]
        self.input_components["agent_0"]=owned+[{"kind":"official_rendering_unknown"}]
        self.trace.log("coordinator_input_attribution",components=self.input_components["agent_0"],
                       messages_sha256=text_hash(json.dumps(messages,ensure_ascii=False,default=str)))
        self.last_return_refs=[]
        self.coordinator_prepared_requests+=1
        return self.input_components["agent_0"]

    def stop_episode(self, error):
        if self.terminal_error is None:
            self.terminal_error=error
            self.trace.log("framework_episode_stopped",exception_type=type(error).__name__,reason=str(error))
            if self.stop_callback is not None:self.stop_callback(error)

    def summary_fields(self):
        return {"agent_turns":self.turns,"worker_steps":self.worker_steps,
            "coordinator_model_inputs_prepared":self.coordinator_prepared_requests,
            "simulation_started_at":self.simulation_started_at,
            "simulation_finished_at":self.time_manager.time() if self.time_manager is not None else None,
            "effective_graph_patches":self.runtime.applied if self.runtime else 0,
            "distinct_agent_instances":sorted(self.runtime.instances) if self.runtime else ["agent_0"],
            "max_active_agents":self.runtime.max_active if self.runtime else 1,
            "works":{k:{"status":w.status,"producer_id":w.producer_id,"repair_attempts":w.repair_attempts} for k,w in self.work_items.items()},
            "risk_assessments":self.risk_observer.assessments,
            "semantic_adoption":"unknown","legitimate_task_completed":"unknown",
            "worker_simulated_generation_time":"NOT_ACCOUNTED_sequential_wall_time_only"}
