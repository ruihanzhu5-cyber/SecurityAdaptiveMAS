"""Free real ARE result checks and explicit model-output fixtures, never API calls."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_attack_five_case import scenarios as scenarios, value_for
from security_adaptive_mas.config import ROOT
from security_adaptive_mas.attacks import AttackContext, AttackRuntime, load_attack_spec, load_for_run
from security_adaptive_mas.trace import TraceLogger


def context(spec,value,tool=None,caller='agent_0'):
    return AttackContext(spec.scenario_id,caller,tool or spec.source_tool,'free_test_call','tool_result',value)


@pytest.mark.parametrize('case',['C1','C2','C4','C5'])
def test_runtime_real_sources_are_exact_and_one_shot(case,scenarios,tmp_path):
    spec=load_for_run('single_entry_tool_result_text',ROOT/f'configs/attack_cases_v0_6_five/{case}.json',scenarios[case].scenario_id)
    value=value_for(scenarios[case],spec)
    original=deepcopy(value)
    app=scenarios[case].get_app(spec.source_tool.split('__')[0]);state=deepcopy(app.get_state())
    runtime=AttackRuntime(spec,TraceLogger(tmp_path/'trace.jsonl'),scenario_id=spec.scenario_id)
    result=runtime.apply(context(spec,value))
    assert result.matched and result.applied and result.attack_event_id
    assert value==original and app.get_state()==state
    assert not runtime.apply(context(spec,value)).applied
    assert runtime.finish()['status']=='INJECTED'


def test_single_email_and_distinct_miss_error_states(scenarios,tmp_path):
    spec=load_attack_spec(ROOT/'configs/attack_cases_v0_6_five/C2.json')
    spec=replace(spec,source_tool='Emails__get_email_by_id',field_path='content')
    value=scenarios['C2'].get_app('Emails').get_email_by_id(spec.source_selector.split(':')[1])
    def new(name,definition=spec):
        return AttackRuntime(definition,TraceLogger(tmp_path/(name+'.jsonl')),scenario_id=spec.scenario_id)
    untouched=new('not_reached')
    assert not untouched.apply(context(spec,value,tool='Contacts__search_contacts')).matched
    assert untouched.finish()['status']=='NOT_REACHED'
    miss=new('miss',replace(spec,source_selector='email_id:missing'))
    assert not miss.apply(context(spec,value)).applied and miss.finish()['status']=='MATCH_MISS'
    bad=new('type')
    with pytest.raises(TypeError,match='BLOCKED_RETURN_TYPE'):
        bad.apply(context(spec,replace(value,content=123)))
    assert bad.finish(interrupted=True)['status']=='ERROR'
    good=new('good')
    applied=good.apply(context(spec,value,caller='worker_1'))
    assert applied.applied and value.content!=applied.modified_result.content


@pytest.mark.parametrize('method',['modular_task_only','modular_risk_aware'])
def test_cli_attack_load_and_benign_does_not_load(method,monkeypatch,capsys):
    from security_adaptive_mas import run_framework
    from security_adaptive_mas.orchestration.configuration import load_design
    design=load_design(ROOT/'configs/research_v0_6.yaml');design['method']=method
    common=['--scenario','scenario_universe_22_0421h2','--dry-run']
    attack=run_framework.main(common+['--attack','single_entry_tool_result_text','--attack-spec',
        str(ROOT/'configs/attack_cases_v0_6_five/C1.json')],design_override=design)
    assert attack['attack_configuration']['attack_id']=='five_v0_6_C1'
    benign=run_framework.main(common+['--attack','none','--attack-spec','intentionally_missing.json'],design_override=design)
    assert benign['attack_configuration'] is None
    capsys.readouterr()


def test_invalid_attack_and_c3_fail_before_model(monkeypatch,tmp_path):
    from security_adaptive_mas import run_framework
    monkeypatch.setattr(run_framework,'DeepSeekClient',lambda *a,**k:pytest.fail('Must fail before constructing model client'))
    path=ROOT/'configs/attack_cases_v0_6_five/C1.json'
    with pytest.raises(ValueError,match='target_case_does_not_match'):
        run_framework.main(['--scenario','scenario_universe_22_kb5ah9','--attack','single_entry_tool_result_text',
                            '--attack-spec',str(path),'--dry-run'])
    with pytest.raises(ValueError,match='mode_surface_mismatch'):
        load_for_run('single_injection',path,'scenario_universe_22_0421h2')
    with pytest.raises(ValueError,match='BLOCKED_ATTACK_SURFACE'):
        load_for_run('single_entry_tool_result_text',ROOT/'configs/attack_cases_v0_6_five/C3_blocked.json','scenario_universe_30_erta5d')
    bad=json.loads(path.read_text(encoding='utf-8'));bad['payload_path']=str(path.parent/'C1_payload.txt')
    bad['evaluation_rule']='unregistered_evaluator'
    config=tmp_path/'bad.json';config.write_text(json.dumps(bad),encoding='utf-8')
    with pytest.raises(ValueError,match='unregistered_attack_evaluator'):
        load_attack_spec(config)


def test_evaluation_does_not_infer_exposure_or_causality(scenarios,tmp_path):
    spec=load_attack_spec(ROOT/'configs/attack_cases_v0_6_five/C2.json')
    trace=TraceLogger(tmp_path/'trace.jsonl')
    runtime=AttackRuntime(spec,trace,scenario_id=spec.scenario_id)
    applied=runtime.apply(context(spec,value_for(scenarios['C2'],spec)))
    events=[json.loads(line) for line in trace.path.read_text(encoding='utf-8').splitlines()]
    result=runtime.evaluate(events,initial={'target_present':True},final={'target_present':False},
        official_success=False,legitimate_task_completed=False,stop_reason='explicit_free_fixture')
    assert result['attack_configured'] and result['attack_injected']
    assert result['delivered_to_runtime'] is False and result['model_submitted_exposure'] is False
    assert result['target_attempted'] is False and result['target_achieved']=='unknown'
    incomplete=runtime.evaluate(events,initial=None,final=None,official_success=None,
        legitimate_task_completed='unknown',stop_reason='interrupted',interrupted=True)
    assert incomplete['model_submitted_exposure']=='unknown' and incomplete['target_attempted']=='unknown'
    from security_adaptive_mas.security import ProvenanceStore
    from security_adaptive_mas.deepseek import DeepSeekClient,RunBudget
    provenance=ProvenanceStore(trace)
    evidence=provenance.add('tool:Emails__list_emails','low_trust',str(applied.modified_result),owner_agent='agent_0')
    runtime.bind_delivery(applied,evidence_id=evidence.evidence_id,context=context(spec,applied.modified_result))
    response=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10,completion_tokens=10),
        choices=[SimpleNamespace(message=SimpleNamespace(content='explicit free exposure fixture'))])
    client=DeepSeekClient(RunBudget(trace,100,800000,.4),completion_fn=lambda **kw:response)
    # Real submission with a related component, but no payload in the messages.
    client.complete([{'role':'user','content':'Only metadata, no external text'}],purpose='coordinator',
        instance_id='agent_0',input_components=[{'object_id':evidence.evidence_id}])
    def score():
        recorded=[json.loads(line) for line in trace.path.read_text(encoding='utf-8').splitlines()]
        return runtime.evaluate(recorded,initial={'target_present':True},final={'target_present':True},
            official_success=False,legitimate_task_completed=False,stop_reason='explicit_free_fixture',model_io_dir=tmp_path/'model_io')
    assert score()['model_submitted_exposure'] is False
    client.complete([{'role':'user','content':str(applied.modified_result)}],purpose='coordinator',
        instance_id='agent_0',input_components=[{'object_id':evidence.evidence_id}])
    assert score()['model_submitted_exposure'] is True and score()['target_attempted'] is False


@pytest.mark.parametrize('method',['modular_task_only','modular_risk_aware'])
def test_formal_cli_to_official_are_to_model_and_evaluator(method,tmp_path,monkeypatch):
    """Known output fixture reads the real target field, then ends. No fabricated injection logs."""
    from security_adaptive_mas import run_framework
    from security_adaptive_mas.deepseek import DeepSeekClient
    from security_adaptive_mas.orchestration.configuration import load_design
    design=load_design(ROOT/'configs/research_v0_6.yaml');design['method']=method
    design['paths']['sandbox_root']=str(tmp_path/'sandbox')
    spec=load_attack_spec(ROOT/'configs/attack_cases_v0_6_five/C1.json')
    coordinator_inputs=[]
    def complete(**kwargs):
        if kwargs.get('response_format')=={'type':'json_object'}:
            content=json.dumps({'suspicion':'unknown','reason_evidence_ids':[],'possible_goal_deviation':'explicit free fixture'})
        else:
            coordinator_inputs.append(kwargs['messages'])
            if len(coordinator_inputs)==1:
                action={'action':spec.source_tool,'action_input':{'conversation_id':spec.source_conversation_id}}
            else:
                action={'action':'AgentUserInterface__send_message_to_user',
                        'action_input':{'content':'Explicit free Attack Harness fixture finished after the source read.'}}
            content='Thought: Explicit free test output fixture\nAction: '+json.dumps(action)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10,completion_tokens=10),
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    monkeypatch.setenv('DEEPSEEK_API_KEY','free-fixture-only')
    monkeypatch.setattr(run_framework,'DeepSeekClient',lambda budget,settings:DeepSeekClient(budget,settings,completion_fn=complete))
    monkeypatch.setattr(run_framework,'TraceLogger',lambda path,mode:TraceLogger(path,'offline_mock'))
    result=run_framework.main(['--scenario',spec.scenario_id,'--attack','single_entry_tool_result_text',
        '--attack-spec',str(ROOT/'configs/attack_cases_v0_6_five/C1.json'),
        '--output-dir',str(tmp_path/'runs'),'--acknowledge-model-cost'],design_override=design)
    summary=result['summaries'][0]
    assert summary['exception_type'] is None
    assert summary['attack_runtime']['status']=='INJECTED'
    score=summary['attack_scoring']
    assert score['attack_injected'] and score['delivered_to_runtime']
    assert score['model_submitted_exposure'] is True and 'agent_0' in score['exposed_instances']
    assert score['target_attempted'] is False and score['target_achieved']=='false'
    assert score['legitimate_task_completed'] is False  # Fixture intentionally omits legitimate work.
    assert summary['effective_graph_patches']==0 and summary['worker_steps']==0
    raw=Path(summary['trace_paths']['framework']).parent
    assert len(coordinator_inputs)==2 and (raw/'official_trace').is_dir()
    (raw/'free_fixture_evidence.json').write_text(json.dumps({'paid_api_requests':0,'summary':summary},indent=2),encoding='utf-8')


def test_old_single_agent_tool_wrapper_keeps_explicit_receiver(scenarios,tmp_path,monkeypatch):
    from security_adaptive_mas.run_attacked_baseline import attacked_builder
    from security_adaptive_mas.attack import AttackOverlay
    from are.simulation.agents.agent_builder import AgentBuilder
    from are.simulation.tool_utils import AppToolAdapter
    spec=load_attack_spec(ROOT/'configs/attack_single_injection_22_kb5ah9.json')
    scenario=scenarios['C5'];state=deepcopy(scenario.get_app('Emails').get_state())
    trace=TraceLogger(tmp_path/'legacy.jsonl')
    overlay=AttackOverlay(spec,trace,scenario_id=spec.scenario_id,receiver_agent_id='agent_0')
    agent=SimpleNamespace(tools=[])
    def initialize(scenario):
        agent.tools=[AppToolAdapter(tool) for tool in scenario.get_tools() if tool._public_name=='Emails__list_emails']
    agent.init_tools=initialize
    monkeypatch.setattr(AgentBuilder,'build',lambda *args,**kwargs:agent)
    built=attacked_builder(SimpleNamespace(),overlay,trace).build(None)
    built.init_tools(scenario)
    result=built.tools[0].forward(folder_name='INBOX',offset=100,limit=100)
    assert overlay.applied
    assert spec.payload_hash and any(Path(spec.payload_path).read_text(encoding='utf-8') in e.content for e in result.emails)
    assert scenario.get_app('Emails').get_state()==state
    worker_only=AttackOverlay(spec,trace,scenario_id=spec.scenario_id)
    assert worker_only.apply_tool_result(result,tool_name='Emails__list_emails',agent_id='agent_0') is result
    assert not worker_only.applied
