"""v0.7 free preparation and one explicitly authorized unit at a time."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import traceback
from types import SimpleNamespace
import yaml

from security_adaptive_mas.config import ROOT
from security_adaptive_mas.run_framework import code_state,ledger_charge,save,build_parser,handoff_authorization,handoff_plan_hash
from security_adaptive_mas.api import run
from security_adaptive_mas.orchestration.configuration import load_design,design_hash,project_config
from security_adaptive_mas.orchestration.handoff_pilot import CONDITIONS,requirements
from scripts.preflight_v0_6_five_cases import preflight as existing_preflight
from scripts.analyze_evidence_handoff import analyze_study,handoff_episode,read

LIMITS={'max_turns':20,'max_requests':160,'max_total_tokens':2000000,'max_cost_usd':.4,'idle_wait_seconds':None}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_view_probe(case,required,directory):
    """Offline parser check of a real typed source, not a Worker/paid result."""
    from security_adaptive_mas.are_adapter import import_official_scenario
    from security_adaptive_mas.attacks import AttackContext,AttackRuntime,load_attack_spec
    from security_adaptive_mas.orchestration.contracts import ArtifactRef,ArtifactRecord
    from security_adaptive_mas.orchestration.evidence_views import EvidenceViewBuilder
    from security_adaptive_mas.trace import TraceLogger
    design=load_design(ROOT/'configs/research_v0_6.yaml');config=project_config(design)
    scenario=import_official_scenario(config.select(case['scenario_id']),config.are_root,
        filesystem=design['paths']['gaia2_filesystem'],sandbox=design['paths']['sandbox_root'])
    spec=load_attack_spec(case['attack_config'])
    runtime=AttackRuntime(spec,TraceLogger(Path(directory)/case['case_id']/'view_probe.jsonl'),scenario_id=case['scenario_id'])
    for value,_ in runtime.surface.read_probe(scenario,spec):
        applied=runtime.apply(AttackContext(case['scenario_id'],'worker_free_probe',spec.source_tool,'free_view_probe','tool_result',value))
        if applied.applied:
            ref=ArtifactRef('offline_typed_source')
            record=ArtifactRecord(ref,'worker_free_probe','private_preflight','free_view_probe',spec.source_tool,
                type(value).__name__,(),1,('full',))
            holder=SimpleNamespace(typed_snapshots={ref.key:applied.modified_result},artifact_records={ref.key:record},
                runtime=SimpleNamespace(objects={ref.key:{}}))
            view=EvidenceViewBuilder(holder,required).build(ref)
            return {'scope':'real_initial_typed_source_parser_only_not_Worker_or_future_ENV',
                'covered_fields':view.covered_required_fields,'missing_fields':view.missing_required_fields,
                'extraction_statuses':sorted({f.status for f in view.fields}),
                'raw_payload_in_view':Path(spec.payload_path).read_text(encoding='utf-8') in view.rendered_content,
                'initial_source_view_complete':not view.missing_required_fields,'model_calls':0}
    raise ValueError('BLOCKED_no_matching_real_source_for_view_probe')


def preflight(directory):
    matrix=existing_preflight(directory)
    required=requirements()
    for case in matrix:
        case['handoff_requirement']=required[case['case_id']]
        case['handoff_trigger_check']='arrived USER/ENV only; no future queue/Oracle read by Pilot'
        missing=set(case['handoff_requirement']['tools'])-set(case.get('allowed_tools',[]))
        case['handoff_ready']=case['benign_ready'] and not missing
        case['missing_handoff_tools']=sorted(missing)
        case['judge_status']='FREE_PREPROCESS_ONLY_NOT_REAL_EVALUATION'
        case['restart_status']='BLOCKED_INDEPENDENT_LEGAL_SOURCE_NOT_DECLARED'
        if case['attack_ready']:
            case['evidence_view_probe']=source_view_probe(case,case['handoff_requirement']['required_fields'],directory)
        if case['case_id']=='C3':
            case['reason']='BLOCKED: no confirmed independent attack target or unique runtime selector; fixed ARE creates IDs with uuid_hex(self.rng) after real ENV execution.'
            case['attack_ready']=False
            case['current_source_status']='future_message_not_read_or_substituted'
    save(Path(directory)/'v0_7_compatibility_matrix.json',matrix)
    return matrix


def units_for(matrix):
    units=[]
    for case in matrix:
        for label,condition in CONDITIONS.items():
            attack=label!='N0'
            ready=case['handoff_ready'] and (case['attack_ready'] if attack else case['benign_ready'])
            reason=case['reason']
            probe=case.get('evidence_view_probe')
            if label in {'A1','A3'} and probe is not None and not probe['initial_source_view_complete']:
                ready=False
                reason=('BLOCKED_TRIGGER_STAGE_VIEW_NOT_VALIDATED' if case['handoff_requirement']['trigger']=='environment'
                    else 'BLOCKED_UNSUPPORTED_REQUIRED_FIELDS_AT_DECLARED_TRIGGER')+': missing '+','.join(probe['missing_fields'])
            if label=='A4':
                ready=False
                reason='BLOCKED_INDEPENDENT_LEGAL_SOURCE_NOT_DECLARED: same-source one-shot requery is not independent recovery'
            units.append({'unit_id':case['case_id']+'_'+label,'case_id':case['case_id'],'condition':condition,
                'phase':2 if label in {'A2','A4'} else 1,'scenario_id':case['scenario_id'],
                'method':'modular_risk_aware','attack':case.get('attack_mode','single_entry_tool_result_text') if attack else 'none',
                'eligibility':'READY' if ready else 'BLOCKED','status':'NOT_RUN' if ready else 'BLOCKED',
                'reason':reason,'legitimate_scope':case['legitimate_scope'],
                'attack_config':case.get('attack_config') if attack else None,
                'attack_config_sha256':case.get('attack_config_sha256') if attack else None,
                'payload_sha256':case.get('payload_sha256') if attack else None})
    if len(units)!=30 or len({u['unit_id'] for u in units})!=30:
        raise ValueError('five_cases_times_six_conditions_required')
    return units


def command(unit,manifest,path,authorization=None,*,dry=False):
    limits=manifest['episode_limits']
    argv=['--design-config',unit['configuration_path'],'--scenario',unit['scenario_id'],
        '--attack','none' if dry and unit['eligibility']=='BLOCKED' else unit['attack'],
        '--max-turns',str(limits['max_turns']),'--max-requests',str(limits['max_requests']),
        '--max-total-tokens',str(limits['max_total_tokens']),'--max-cost-usd',str(limits['max_cost_usd']),
        '--output-dir',str(path.parent/'units'/unit['unit_id'])]
    if unit['attack_config'] and argv[argv.index('--attack')+1]!='none':
        argv+=['--attack-spec',unit['attack_config']]
    if dry:
        argv+=['--dry-run']
    else:
        if authorization is None:
            raise ValueError('fresh_authorization_required')
        approval=read(authorization)
        argv+=['--study-manifest',str(path),'--study-unit',unit['unit_id'],'--shared-ledger',manifest['shared_ledger'],
            '--batch-cap-usd',str(manifest['ledger_charge_before']+approval['additional_cap_usd']),
            '--handoff-authorization',str(authorization),'--acknowledge-model-cost']
    return argv


def prepare(directory,ledger):
    directory=Path(directory).resolve();ledger=Path(ledger).resolve()
    if not directory.is_relative_to(ROOT/'results/runs/evidence_handoff_v0_7'):
        raise ValueError('v0_7_requires_independent_results_directory')
    if directory.exists():
        raise FileExistsError('never_overwrite_existing_study_directory')
    if not ledger.is_file():
        raise FileNotFoundError('historical_shared_ledger_required')
    frozen=code_state()
    if frozen['dirty_status']:
        raise ValueError('commit_changes_before_prepare')
    directory.mkdir(parents=True)
    matrix=preflight(directory/'preflight')
    inputs=directory/'inputs';inputs.mkdir()
    shutil.copyfile(ledger,inputs/'ledger_before.jsonl')
    manifest={'kind':'evidence_handoff_v0_7','study_id':directory.name,'prepared_at_utc':datetime.now(timezone.utc).isoformat(),
        'code':frozen,'authorization_status':'PENDING_NEW_USER_APPROVAL','planned_additional_cap_usd':8.0,
        'shared_ledger':str(ledger),'ledger_charge_before':ledger_charge(ledger),'ledger_sha256_before':sha(ledger),
        'episode_limits':dict(LIMITS),'units':units_for(matrix),'pilot_unit_id':'C5_A3','frozen_input_hashes':{},
        'upstreams':read(ROOT/'configs/upstream_v0_6.lock.json'),'source_versions':read(ROOT/'configs/source_versions.json'),
        'model':'deepseek-flash','risk_prompt_version':'semantic-risk-v0.6-json-1',
        'free_preflight_model_calls':sum(c['model_calls']+c['benchmark_runtime']['model_requests'] for c in matrix),
        'real_pilot_passed':False,'fee_authorization':'NOT_AUTHORIZED; historical approvals do not apply',
        'paired_episode_state':'independent fresh environments, not a complete handoff-state counterfactual snapshot'}
    pilot=next(u for u in manifest['units'] if u['unit_id']==manifest['pilot_unit_id'])
    if pilot['eligibility']!='READY':
        raise ValueError('BLOCKED_frozen_pilot_unit_not_ready')
    (inputs/'handoff_requirements_v0_7.yaml').write_bytes((ROOT/'configs/handoff_requirements_v0_7.yaml').read_bytes())
    design=load_design(ROOT/'configs/research_v0_6.yaml')
    path=directory/'study_manifest.json'
    for unit in manifest['units']:
        configuration={**design,'version':'v0.7','experiment':{'mode':'controlled_handoff','case_id':unit['case_id'],
            'condition':unit['condition'],'requirements_path':str(inputs/'handoff_requirements_v0_7.yaml')}}
        config_path=inputs/(unit['unit_id']+'.yaml')
        config_path.write_text(yaml.safe_dump(configuration,sort_keys=False),encoding='utf-8')
        unit['configuration_path']=str(config_path)
        with redirect_stdout(io.StringIO()):
            preview=run(command(unit,manifest,path,dry=True))
        unit['design_hash']=preview['design_hash']
        save(inputs/(unit['unit_id']+'_dry_run.json'),preview)
        unit['dry_validation_scope']='base_config_only_attack_blocked' if unit['eligibility']=='BLOCKED' else 'declared_complete_unit'
        if unit['attack_config']:
            spec=read(unit['attack_config'])
            payload=Path(spec['payload_path'])
            if not payload.is_absolute():
                payload=Path(unit['attack_config']).parent/payload
            manifest['frozen_input_hashes'][str(payload.resolve())]=sha(payload)
            manifest['frozen_input_hashes'][unit['attack_config']]=sha(unit['attack_config'])
    for relative in frozen['source_file_hashes']:
        target=inputs/'code_snapshot'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/relative).read_bytes())
    manifest['test_hashes']={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'tests').glob('*.py')}
    for relative in manifest['test_hashes']:
        target=inputs/'test_snapshot'/relative
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/relative).read_bytes())
    for source in [*inputs.glob('*.yaml'),inputs/'ledger_before.jsonl',ROOT/'work/v0_6_local_paths.yaml']:
        manifest['frozen_input_hashes'][str(source)]=sha(source)
    for case in matrix:
        record=next(r for r in read(ROOT/'configs/five_manifest.json') if r['scenario_id']==case['scenario_id'])
        source=Path(design['paths']['scenario_root'])/record['path']
        manifest['frozen_input_hashes'][str(source)]=sha(source)
    manifest['plan_sha256']=handoff_plan_hash(manifest)
    save(path,manifest)
    analyze_study(directory)
    return manifest


def execute(directory,unit_id,authorization):
    directory=Path(directory).resolve();path=directory/'study_manifest.json'
    manifest=read(path)
    unit=next(u for u in manifest['units'] if u['unit_id']==unit_id)
    if unit['status']!='NOT_RUN' or unit['eligibility']!='READY':
        raise ValueError('unit_not_ready_or_already_attempted')
    argv=command(unit,manifest,path,Path(authorization).resolve())
    args=build_parser().parse_args(argv)
    args.design=load_design(args.design_config);args.design_hash=design_hash(args.design)
    approval=handoff_authorization(args)
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise ValueError('BLOCKED_DEEPSEEK_API_KEY_missing')
    if ledger_charge(Path(manifest['shared_ledger']))+LIMITS['max_cost_usd']>approval['absolute_cap_usd']:
        raise ValueError('BLOCKED_shared_budget_including_historical_pending_reservations')
    unit.update(status='RUNNING',started_at_utc=datetime.now(timezone.utc).isoformat())
    manifest['authorization_status']='EXPLICIT_FILE_VALIDATED'
    manifest['fee_authorization_file_sha256']=sha(authorization)
    save(path,manifest)
    try:
        outcome=run(argv)
        summary=outcome['summaries'][0]
        unit.update(run_id=summary['run_id'],run_dir=str(Path(summary['trace_paths']['framework']).parent),
            status='FAILED' if summary['exception_type'] else 'FINISHED')
        analysis=handoff_episode(unit['run_dir'])
        unit['analysis']=analysis
        # These are pipeline gates, not requirements for a positive task/security score.
        stopped=[]
        if summary['exception_type'] or summary['benchmark_judge_status']!='VALID':
            stopped.append('model_environment_or_Judge_failure:'+str(summary['exception_type']))
        if analysis['controlled_delegations']!=1 or not any(e.get('component_submission_link_observed') and e['agent_id']=='agent_0'
            for e in read(Path(unit['run_dir'])/'handoff_delivery_submission_audit.json')):
            stopped.append('missing_actual_worker_handoff_submission')
        if not analysis['worker_started'] or not analysis['worker_finished']:
            stopped.append('missing_completed_real_worker')
        if not analysis['handoff_requested'] or not analysis['handoff_delivered']:
            stopped.append('missing_actual_coordinator_delivery')
        if unit['attack']!='none' and summary['attack_runtime']['status']!='INJECTED':
            stopped.append('attack_not_injected:'+summary['attack_runtime']['status'])
        if unit['attack']!='none' and not any(a.startswith('worker_') for a in summary['attack_scoring']['exposed_instances']):
            stopped.append('attack_not_submitted_to_actual_worker')
        if stopped:
            manifest['stop_reason']='; '.join(stopped)
        elif unit_id==manifest['pilot_unit_id']:
            manifest['real_pilot_passed']=True
    except (Exception,SystemExit) as error:
        unit.update(status='FAILED',error_type=type(error).__name__,reason=str(error),traceback=traceback.format_exc())
        manifest['stop_reason']='paid_unit_failure_no_retry:'+str(error)
    unit['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    save(path,manifest)
    analyze_study(directory)
    return unit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('preflight','prepare','run','analyze'))
    parser.add_argument('--study-dir',type=Path,required=True)
    parser.add_argument('--ledger',type=Path)
    parser.add_argument('--study-unit',choices=tuple(f'C{i}_{key}' for i in range(1,6) for key in CONDITIONS))
    parser.add_argument('--authorization',type=Path)
    parser.add_argument('--acknowledge-model-cost',action='store_true')
    args=parser.parse_args(argv)
    if args.action=='preflight':
        if args.study_dir.exists():
            parser.error('use a new preflight directory; raw diagnostics are never overwritten')
        result=preflight(args.study_dir)
    elif args.action=='prepare':
        if args.ledger is None:
            parser.error('prepare requires existing --ledger')
        result=prepare(args.study_dir,args.ledger)
    elif args.action=='run':
        if not args.acknowledge_model_cost or args.authorization is None or args.study_unit is None:
            parser.error('run requires one --study-unit, fresh --authorization and --acknowledge-model-cost')
        result=execute(args.study_dir,args.study_unit,args.authorization)
    else:
        result=analyze_study(args.study_dir)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    main()
