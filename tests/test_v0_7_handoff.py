"""Real ARE / AOrchestra seams with explicit free output fixtures only."""
import json
from pathlib import Path
from types import SimpleNamespace

from security_adaptive_mas.config import ROOT
from security_adaptive_mas.deepseek import DeepSeekClient
from security_adaptive_mas.orchestration.configuration import load_design,project_config,design_hash
from security_adaptive_mas.trace import TraceLogger


def response(text):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10,completion_tokens=10),
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_p0_real_worker_get_artifact_and_submitted_observation(tmp_path,monkeypatch):
    from security_adaptive_mas import run_framework as framework
    design=load_design(ROOT/'configs/research_v0_6.yaml')
    design.update(version='v0.7',method='modular_task_only',experiment={
        'mode':'controlled_handoff','condition':'clean_full','case_id':'C5'})
    design['paths']['sandbox_root']=str(tmp_path/'sandbox')
    coordinator=[]
    steps=[]
    def complete(**kwargs):
        if kwargs.get('response_format')=={'type':'json_object'}:
            if kwargs['messages'][0]['role']=='system':
                return response(json.dumps({'suspicion':'not_supported','reason_evidence_ids':[],
                    'possible_goal_deviation':'explicit free fixture'}))
            steps.append(kwargs['messages'])
            action={'action':'Emails__list_emails','params':{'folder_name':'INBOX','offset':0,'limit':1},'memory':'read real page'} if len(steps)==1 else {
                'action':'finish','params':{'status':'done','result':'FREE_REAL_WORKER_ARTIFACT','evidence_ids':[],
                    'uncertainties':['Explicit free fixture, not task completion']},'memory':'finished read'}
            return response(json.dumps(action))
        coordinator.append(kwargs['messages'])
        return response('Thought: Explicit free fixture\nAction: '+json.dumps({
            'action':'AgentUserInterface__send_message_to_user','action_input':{'content':'Free handoff wiring fixture ended.'}}))
    monkeypatch.setenv('DEEPSEEK_API_KEY','free-fixture-only')
    monkeypatch.setattr(framework,'DeepSeekClient',lambda budget,settings:DeepSeekClient(budget,settings,completion_fn=complete))
    monkeypatch.setattr(framework,'TraceLogger',lambda path,mode:TraceLogger(path,'offline_mock'))
    args=framework.build_parser().parse_args(['--scenario','scenario_universe_22_kb5ah9'])
    args.design,args.design_hash=design,design_hash(design)
    summary=framework.run_case(args,project_config(design),args.scenario,tmp_path/'episode',framework.code_state())
    assert summary['exception_type'] is None
    assert len(steps)==2 and len(coordinator)==1
    assert 'FREE_REAL_WORKER_ARTIFACT' in json.dumps(coordinator[0])
    trace=Path(summary['trace_paths']['framework'])
    events=[json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
    assert any(e['kind']=='worker_started' for e in events)
    assert any(e['kind']=='tool_completed' and e['agent_id'].startswith('worker_') for e in events)
    assert any(e['kind']=='controlled_handoff_consumption' and e['actual_get_artifact'] for e in events)
    assert any(e['kind']=='manta_packet_selection' for e in events)
    assert any(e['kind']=='mutation_validation' and e['structural_validation']=='passed' for e in events)
    sent=[e for e in events if e['kind']=='model_request_submitted' and e['purpose']=='coordinator']
    assert sent[0]['prepared_input_components']
    assert all(e.get('initiation_mode')=='controlled_handoff' for e in events if e['kind']=='delegation_requested')


def test_p0_future_environment_is_not_a_trigger(make_session):
    # Existing v0.6 Session fixture remains autonomous. Enable only the pilot
    # object to check arrival semantics without manufacturing an ARE event.
    from security_adaptive_mas.orchestration.handoff_pilot import ControlledHandoffPilot
    session,_,_=make_session()
    pilot=ControlledHandoffPilot(session,{'case_id':'C1'})
    assert pilot.plan_if_arrived() is None
    assert not session.work_items


from test_v0_6_modular import make_session as make_session  # noqa: E402
