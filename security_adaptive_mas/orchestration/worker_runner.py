"""Serial real-tool runner around AOrchestra reset/step/Memory."""
import asyncio
from dataclasses import dataclass, field
import json
from ..adapters.aorchestra import BudgetedWorkerLLM, ObservedReActAgent, Memory, BasicInfo
from .contracts import ArtifactRef, string_list


@dataclass
class StepRecord:
    info: dict = field(default_factory=dict)


def run_sync(session, work):
    try:asyncio.get_running_loop()
    except RuntimeError:pass
    else:raise RuntimeError("UNSUPPORTED_nested_event_loop")
    return asyncio.run(run_worker(session,work))


async def run_worker(s,work):
    worker_id=work.producer_id
    limit=s.config["worker_max_steps"]
    llm=BudgetedWorkerLLM(s.client,s,worker_id,work.work_id)
    actions=list(work.requested_tools)+["get_artifact","finish"]
    blocks=[f"### {name}\n{s.original_tools[name].description}\ninputs={json.dumps(s.original_tools[name].inputs)}" for name in work.requested_tools]
    blocks += ['### get_artifact\nparams: artifact_id:string, view:string, for_work_id:string, required_fields:list[str]',
        '### finish\nparams: status:done|partial, result:string, evidence_ids:list[str], uncertainties:list[str]']
    handles=[{"object_id":key,"views":list(values)} for key,values in s.handles.get(worker_id,{}).items()
             if key!=work.instruction_evidence_id]
    context=json.dumps({"work_id":work.work_id,"artifact_handles":handles,"instruction":"Call get_artifact to read a handle; IDs alone contain no result"})
    worker=ObservedReActAgent(llm=llm,benchmark_type="gaia",task_instruction=s.provenance.items[work.instruction_evidence_id].text,
        context=context,original_question=s.goal_text(),allowed_tools=actions,
        memory=Memory(llm,max_memory=limit+1,keep_recent=3))
    worker.reset(BasicInfo(env_id="shared_are",instruction=s.goal_text(),action_space="\n\n".join(blocks),max_steps=limit,
        meta_data={"work_id":work.work_id,"worker_id":worker_id}))
    goal_refs=[ArtifactRef(event.evidence_id) for event in s.observation_recorder.snapshot().user_events]
    instruction_ref=ArtifactRef(work.instruction_evidence_id)
    history=[]
    observation={"status":"started","work_id":work.work_id}
    observation_refs=[]
    memory_refs=[]
    available_results=[]
    for index in range(limit):
        llm.prompt_refs=[instruction_ref,*goal_refs,*observation_refs,*memory_refs]
        action,raw,prompt=await worker.step(observation,history,current_step=index,max_steps=limit)
        s.worker_steps+=1
        name,params=action["action"],action["params"]
        s.trace.log("worker_action",instance_id=worker_id,work_id=work.work_id,step=index,action=action,
                    raw_response=raw,prompt=prompt)
        parents=tuple(dict.fromkeys(ref.object_id for ref in llm.prompt_refs))
        memory=s.provenance.add("worker:memory","derived",worker.memory.as_text(),parents=parents,owner_agent=worker_id)
        memory_ref=s.register_artifact(memory,worker_id,work.work_id,payload_kind="worker_memory",parents=tuple(llm.prompt_refs),fields=("memory",))
        memory_refs=[memory_ref]
        if name not in actions:raise PermissionError("worker_action_not_exactly_authorized")
        if index==limit-1 and name!="finish":raise ValueError("worker_final_step_requires_finish")
        if name=="finish":
            if set(params)!={"status","result","evidence_ids","uncertainties"} or params["status"] not in {"done","partial"} or not isinstance(params["result"],str):
                raise ValueError("worker_finish_schema")
            ids=string_list(params["evidence_ids"],"finish_evidence_ids")
            string_list(params["uncertainties"],"uncertainties")
            known={key.split("@")[0] for key in s.seen.get(worker_id,set())}|set(parents)
            if not set(ids)<=known:raise ValueError("worker_finish_cites_unseen_evidence")
            payload=json.dumps(params,ensure_ascii=False)
            result_parents=tuple(dict.fromkeys([*parents,*ids]))
            result=s.provenance.add("worker:result","derived",payload,parents=result_parents,owner_agent=worker_id)
            if s.evidence_builder is not None:
                issued_refs={r.ref.object_id:r.ref for r in s.artifact_records.values()}
                parent_refs=tuple(issued_refs[key] for key in result_parents)
            else:
                parent_refs=tuple(ArtifactRef(key) for key in result_parents)
            result_ref=s.register_artifact(result,worker_id,work.work_id,payload_kind="worker_result",
                parents=parent_refs,fields=work.output_fields or ("result",))
            s.risk_states[result_ref.key]=s.risk_observer.assess(worker_id,(result_ref.object_id,))
            if s.evidence_builder is not None:
                s.evidence_builder.ensure(result_ref)
            work.status="artifact_ready" if params["status"]=="done" else "partial"
            return list(dict.fromkeys([result_ref,*available_results])),index+1
        if name=="get_artifact":
            if set(params)!={"artifact_id","view","for_work_id","required_fields"}:raise ValueError("worker_get_artifact_schema")
            outcome=s.request_delivery(worker_id,params)
            observation_refs=list(s.last_return_refs)
            observation=outcome
        else:
            result,ref=s.invoke_tool(worker_id,work.work_id,name,params)
            observation={"tool":name,"result":str(result),"evidence_id":ref.object_id}
            observation_refs=[ref]
            available_results.append(ref)
        history.append(StepRecord({"last_action_result":observation}))
    work.status="partial"
    return available_results,limit
