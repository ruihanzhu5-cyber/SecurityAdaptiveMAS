"""Inject the shared budgeted client into the pinned ReAct fork."""
from .._vendor.aorchestra.aorchestra.subagents.react_agent import ReActAgent
from .._vendor.aorchestra.base.agent.memory import Memory
from .._vendor.aorchestra.benchmark.common.env import BasicInfo


class BudgetedWorkerLLM:
    def __init__(self,client,session,worker_id,work_id):
        self.client,self.session,self.worker_id,self.work_id=client,session,worker_id,work_id
        self.prompt_refs=[]

    def capture_inputs(self,prompt):
        self.session.input_components[self.worker_id]=[
            self.session.component(ref) for ref in dict.fromkeys(self.prompt_refs)]
        self.session.trace.log("worker_prompt_composed",instance_id=self.worker_id,work_id=self.work_id,
            prompt=prompt,components=self.session.input_components[self.worker_id])

    async def __call__(self,prompt):
        components=self.session.worker_input_components(self.worker_id,prompt)
        response=self.client.complete([{"role":"user","content":prompt}],response_format={"type":"json_object"},
            purpose="worker:"+self.worker_id,input_components=components,instance_id=self.worker_id,activation_id=self.work_id,
            on_submitted=lambda request_id:self.session.record_submission(self.worker_id,self.work_id,components,request_id))
        content=response.choices[0].message.content
        if not isinstance(content,str) or not content.strip():raise ValueError("worker_empty_model_response")
        return content


class ObservedReActAgent(ReActAgent):
    def _build_prompt(self,*args,**kwargs):
        prompt=super()._build_prompt(*args,**kwargs)
        self.llm.capture_inputs(prompt)
        return prompt
