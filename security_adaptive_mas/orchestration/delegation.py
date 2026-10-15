"""ARE-native control tools bound to authenticated runtime identities."""
from are.simulation.tools import Tool


class DelegateTaskTool(Tool):
    name="delegate_task"
    description="Delegate a concrete bounded read-only task. Returns work status and artifact handles, never result content. Use get_artifact to request content."
    inputs={"task_instruction":{"type":"string","description":"Concrete task proposal"},
            "context_artifact_ids":{"type":"any","description":"Unique list of granted artifact IDs, no inline body"},
            "tools":{"type":"any","description":"Unique list of exact authorized read-only application tool names"},
            "output_fields":{"type":"any","description":"Unique list of expected result fields"},
            "model":{"type":"string","description":"Must be deepseek-flash"}}
    output_type="any"

    def __init__(self,session,caller_id="agent_0"):
        super().__init__()
        self.session,self.caller_id=session,caller_id

    def forward(self,task_instruction:str,context_artifact_ids,tools,output_fields,model:str):
        try:
            result=self.session.request_delegation(self.caller_id,dict(task_instruction=task_instruction,
                context_artifact_ids=context_artifact_ids,tools=tools,output_fields=output_fields,model=model))
            self.session.last_return_refs=[]
            return result
        except Exception as error:
            self.session.stop_episode(error)
            raise


class GetArtifactTool(Tool):
    name="get_artifact"
    description="Request an issued artifact handle or a permitted view for current work. Held/repairing does not mean task completion."
    inputs={"artifact_id":{"type":"string","description":"Issued artifact ID"},
            "view":{"type":"string","description":"Permitted view, e.g. full or email_metadata_v1"},
            "for_work_id":{"type":"string","description":"root for Coordinator, actual work ID for Worker"},
            "required_fields":{"type":"any","description":"Unique list of required fields; empty requests full chosen view"}}
    output_type="any"

    def __init__(self,session,caller_id="agent_0"):
        super().__init__()
        self.session,self.caller_id=session,caller_id

    def forward(self,artifact_id:str,view:str,for_work_id:str,required_fields):
        try:return self.session.request_delivery(self.caller_id,dict(artifact_id=artifact_id,view=view,for_work_id=for_work_id,required_fields=required_fields))
        except Exception as error:
            self.session.stop_episode(error)
            raise
