"""Free study/fee gates; no credentials, live ledger writes or paid requests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest

from security_adaptive_mas.config import ROOT
from security_adaptive_mas import run_framework as framework
from security_adaptive_mas.orchestration.configuration import load_design,design_hash
from scripts.run_evidence_handoff_study import units_for,LIMITS,command


def matrix():
    records=json.loads((ROOT/'configs/five_manifest.json').read_text(encoding='utf-8'))
    return [{'case_id':f'C{i}','scenario_id':r['scenario_id'],'handoff_ready':True,'benign_ready':True,
        'attack_ready':i!=3,'reason':'explicit free test','legitimate_scope':'PARTIAL_SCOPE' if i==4 else 'FULL',
        'attack_config':str(ROOT/f'configs/attack_cases_v0_6_five/C{i}.json') if i!=3 else None}
        for i,r in enumerate(records,1)]


def test_thirty_rows_preserve_blocked_and_partial_scope():
    units=units_for(matrix())
    assert len(units)==30 and sum(u['phase']==1 for u in units)==20
    assert sum(u['eligibility']=='READY' for u in units)==21
    assert all(u['status']=='BLOCKED' for u in units if u['case_id']=='C3' and u['attack']!='none')
    assert all(u['status']=='BLOCKED' for u in units if u['unit_id'].endswith('_A4'))
    assert all(u['legitimate_scope']=='PARTIAL_SCOPE' for u in units if u['case_id']=='C4')
    actual=matrix()
    for case in actual[:2]:
        case['evidence_view_probe']={'initial_source_view_complete':False,'missing_fields':['date_start','date_end']}
        case['handoff_requirement']={'trigger':'environment' if case['case_id']=='C1' else 'user'}
    blocked=units_for(actual)
    assert sum(u['eligibility']=='READY' for u in blocked)==17
    assert all(u['status']=='BLOCKED' for u in blocked if u['unit_id'] in {'C1_A1','C1_A3','C2_A1','C2_A3'})


def test_legacy_caps_unchanged_and_new_version_needs_new_approval(monkeypatch,capsys):
    design=load_design(ROOT/'configs/research_v0_6.yaml')
    common=['--scenario','scenario_universe_22_0421h2','--dry-run','--max-requests','160','--max-total-tokens','2000000']
    with pytest.raises(SystemExit):
        framework.main(common,design_override=design)
    design.update(version='v0.7',experiment={'case_id':'C1','condition':'attacked_joint','mode':'controlled_handoff'})
    preview=framework.main(common,design_override=design)
    assert preview['max_requests']==160 and preview['max_total_tokens']==2000000 and preview['max_cost_usd']==.4
    monkeypatch.setattr(framework,'DeepSeekClient',lambda *a,**kw:pytest.fail('fee guard must run before model construction'))
    with pytest.raises(ValueError,match='requires_fresh_study_fee_authorization'):
        framework.main(common[:2]+['--acknowledge-model-cost'],design_override=design)
    capsys.readouterr()


@pytest.fixture
def authorized_fixture(tmp_path,monkeypatch):
    directory=tmp_path/'results/runs/evidence_handoff_v0_7/free_contract_fixture'
    directory.mkdir(parents=True)
    ledger=tmp_path/'historical.jsonl'
    ledger.write_text('{"ledger_id":"historical_pending","cost_estimate_usd":0.4,"unknown_cost_bound":0}\n',encoding='utf-8')
    inputs=directory/'inputs';inputs.mkdir()
    prefix=inputs/'ledger_before.jsonl';prefix.write_bytes(ledger.read_bytes())
    frozen={'code_snapshot_id':'explicit_free_contract_snapshot','dirty_status':''}
    monkeypatch.setattr(framework,'ROOT',tmp_path)
    monkeypatch.setattr(framework,'code_state',lambda:frozen)
    design=load_design(ROOT/'configs/research_v0_6.yaml')
    design.update(version='v0.7',experiment={'case_id':'C1','condition':'attacked_joint','mode':'controlled_handoff'})
    units=units_for(matrix())
    for unit in units:
        unit.update(configuration_path=str(inputs/(unit['unit_id']+'.yaml')),design_hash=design_hash(design))
    manifest={'kind':'evidence_handoff_v0_7','study_id':directory.name,'code':frozen,'units':units,
        'shared_ledger':str(ledger),'ledger_charge_before':.4,'episode_limits':dict(LIMITS),
        'planned_additional_cap_usd':8.0,'frozen_input_hashes':{str(prefix):hashlib.sha256(prefix.read_bytes()).hexdigest()},
        'real_pilot_passed':False}
    manifest['plan_sha256']=framework.handoff_plan_hash(manifest)
    path=directory/'study_manifest.json';framework.save(path,manifest)
    authorization=directory/'explicit_test_approval.json'
    approval={'kind':'v0_7_fee_authorization','study_id':directory.name,'code_snapshot_id':frozen['code_snapshot_id'],
        'plan_sha256':manifest['plan_sha256'],'additional_cap_usd':.4,'authorized_units':['C1_A3'],
        'phase2_authorized':False,'user_confirmation':'FREE CONTRACT FIXTURE, not live model authorization'}
    framework.save(authorization,approval)
    unit=next(u for u in units if u['unit_id']=='C1_A3')
    args=framework.build_parser().parse_args(command(unit,manifest,path,authorization))
    args.design=design;args.design_hash=design_hash(design)
    return args,manifest,path,authorization,ledger


@pytest.mark.parametrize('mutation',['none','claim','ledger','config','condition','old_approval','plan'])
def test_authorization_snapshot_ledger_and_single_use_contract(authorized_fixture,mutation):
    args,manifest,path,authorization,ledger=authorized_fixture
    original=ledger.read_bytes()
    if mutation=='claim':
        claim=path.parent/'claims/C1_A3.claim.json';claim.parent.mkdir();claim.write_text('{}')
    elif mutation=='ledger':
        ledger.write_text('')
    elif mutation=='config':
        args.design_hash='changed'
    elif mutation=='condition':
        args.design=deepcopy(args.design);args.design['experiment']['condition']='attacked_graph_only'
    elif mutation=='old_approval':
        approval=json.loads(authorization.read_text());approval['kind']='v0_6_C1_B1_single_validation';framework.save(authorization,approval)
    elif mutation=='plan':
        manifest['episode_limits']['max_cost_usd']=2.0;framework.save(path,manifest)
    if mutation=='none':
        validated=framework.handoff_authorization(args)
        assert validated['absolute_cap_usd']==.8
        assert framework.ledger_charge(ledger)==.4 and ledger.read_bytes()==original
        assert not Path(validated['claim']).exists()  # Validation reserves nothing.
    else:
        with pytest.raises(ValueError):
            framework.handoff_authorization(args)
