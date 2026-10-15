"""Default-off experimental trigger; all work uses the existing delegation tools."""
from dataclasses import dataclass
from pathlib import Path
import yaml
from ..config import ROOT


CONDITIONS = {
    'N0':'clean_full', 'A0':'attacked_full', 'A1':'attacked_context_only',
    'A2':'attacked_graph_only', 'A3':'attacked_joint', 'A4':'attacked_delete_restart',
}


def requirements(path=None):
    path=ROOT/'configs/handoff_requirements_v0_7.yaml' if path is None else Path(path)
    value=yaml.safe_load(path.read_text(encoding='utf-8'))
    if value['version']!='v0.7':
        raise ValueError('unsupported_handoff_requirements_version')
    return value['cases']


@dataclass(frozen=True)
class HandoffPlan:
    case_id: str
    goal_revision: int
    tools: tuple[str,...]
    work_instruction: str
    required_fields: tuple[str,...]
    stage: str
    source_refs: tuple
    trigger_evidence_id: str
    trigger_event_id: str | None
    requested_consumer: str = 'agent_0'


class ControlledHandoffPilot:
    def __init__(self,session,experiment):
        self.s=session
        self.experiment=experiment
        self.requirement=requirements(experiment.get('requirements_path'))[experiment['case_id']]
        self.started=False
        self.outcome=None
        self.receipt=None
        self.selected_refs=[]

    def plan_if_arrived(self):
        from .contracts import ArtifactRef
        s=self.s
        snapshot=s.observation_recorder.snapshot()
        if self.started or not snapshot.user_events:
            return None
        arrived=[e for e in snapshot.events if e.kind==self.requirement['trigger']]
        if not arrived:
            return None
        tools=tuple(self.requirement['tools'])
        if not set(tools)<=s.runtime.worker_tools:
            raise PermissionError('controlled_handoff_required_read_tools_unavailable')
        event=arrived[-1]
        refs=tuple(ArtifactRef(e.evidence_id) for e in snapshot.events if e.kind in {'user','environment'})
        context='\n'.join(s.provenance.items[r.object_id].text for r in refs)
        instruction=self.requirement['instruction']+'\nUse only the authorized read-only tools. '+\
            'Return the real tool evidence IDs and uncertainties.\nAlready arrived task/context:\n'+context
        return HandoffPlan(self.experiment['case_id'],s.goal_revision,tools,instruction,
            tuple(self.requirement['required_fields']),event.kind,refs,event.evidence_id,event.event_id)

    def maybe_run(self):
        plan=self.plan_if_arrived()
        if plan is None:
            return None
        self.started=True
        self.s.trace.log('controlled_handoff_trigger',mode='controlled_handoff',case_id=plan.case_id,
            trigger_basis=plan.stage,trigger_event_id=plan.trigger_event_id,
            trigger_evidence_id=plan.trigger_evidence_id,source_visible_before_trigger=True)
        self.receipt=self.s.start_controlled_work(plan)
        work=self.s.work_items[self.receipt['work_id']]
        self.s.trace.log('controlled_handoff_work_bound',mode='controlled_handoff',case_id=plan.case_id,
            trigger_basis=plan.stage,trigger_event_id=plan.trigger_event_id,
            trigger_evidence_id=plan.trigger_evidence_id,event_id_status='unavailable' if plan.trigger_event_id is None else 'observed',
            work_id=work.work_id,worker_id=work.producer_id,source_visible_before_trigger=True)
        if not work.result_refs:
            self.outcome={'status':'held','reason':'worker_produced_no_artifact','work_id':work.work_id}
            return self.outcome
        from .delegation import GetArtifactTool
        original=work.result_refs[0]
        self.outcome=GetArtifactTool(self.s).forward(original.object_id,'full','root',list(plan.required_fields))
        self.selected_refs=list(self.s.last_return_refs)
        self.s.trace.log('controlled_handoff_consumption',work_id=work.work_id,worker_id=work.producer_id,
            original_artifact_ref=original.key,status=self.outcome['status'],
            selected_view_refs=[r.key for r in self.selected_refs],actual_get_artifact=True)
        return self.outcome
