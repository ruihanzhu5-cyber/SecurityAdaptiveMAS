"""Read-only evidence accounting; byte proxies never become actual Token savings."""
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path

from scripts.analyze_v0_6_context_growth import analyze_episode
from security_adaptive_mas.run_framework import save,ledger_charge


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def export_csv(path,rows):
    columns=list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,columns)
        writer.writeheader()
        writer.writerows({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows)


def handoff_episode(directory):
    directory=Path(directory)
    result=analyze_episode(directory)
    events=[json.loads(line) for line in (directory/'framework_trace.jsonl').read_text(encoding='utf-8').splitlines()]
    summary=read(directory/'summary.json')
    config=read(directory/'run_config.json')
    evidence={e['evidence_id']:e for e in events if e['kind']=='evidence'}
    records={e['ref']['object_id']:e for e in events if e['kind']=='artifact_registered'}
    requests=read(directory/'context_growth.json')['requests']
    seen=defaultdict(set)
    measurements=[]
    submitted={e['request_id'].split(':')[0]:e for e in events if e['kind']=='model_request_submitted'}
    for row in requests:
        request=read(directory/'model_io'/(row['prepared_id']+'_request.json'))
        categories=Counter();repeat=Counter();unknown=[]
        for component in request.get('components') or []:
            oid=component.get('object_id')
            if oid is None:
                unknown.append(component.get('kind','unclassified'))
                continue
            record=records[oid];item=evidence[oid]
            kind=record['payload_kind']
            category=('approved_view' if kind in {'task_evidence_v1','email_metadata_v1'} else
                'worker_memory' if kind=='worker_memory' else 'worker_result' if kind=='worker_result' else
                'task_instruction' if kind in {'notification','work_instruction'} else 'raw_tool_or_derived_source')
            key=(oid,component.get('version'),component.get('view_id'))
            size=len(item['text'].encode())
            categories[category]+=size
            if key in seen[request['instance_id']]:
                repeat[category]+=size
            if row['submitted']:
                seen[request['instance_id']].add(key)
        payload={key:request[key] for key in ('messages','tools','response_format')}
        byte_bound=len(json.dumps(payload,ensure_ascii=False).encode())
        output_bound=config['settings']['max_output_tokens']
        measurements.append({**row,'budget_input_byte_bound':byte_bound,'budget_output_token_bound':output_bound,
            'budget_request_cost_bound_usd':byte_bound*config['design']['rates']['input_usd_per_token']+
                output_bound*config['design']['rates']['output_usd_per_token'],
            'component_bytes_proxy':dict(categories),'repeated_component_bytes_proxy':dict(repeat),
            'unknown_component_categories':unknown,'component_sizes_are_additive':False,
            'byte_bound_is_actual_tokens':False})
    deliveries=[]
    for event in events:
        if event['kind']!='runtime_object_delivered':
            continue
        links=[]
        for sent in submitted.values():
            if sent.get('instance_id')!=event['agent_id'] or sent['time_utc']<event['time_utc']:
                continue
            if any(c.get('object_id')==event['object_id'] and c.get('view_id')==event['view_id'] and
                c.get('rendered_payload_hash')==event['payload_sha256'] for c in sent.get('prepared_input_components') or []):
                links.append(sent['request_id'])
        deliveries.append({**event,'model_request_ids':links,'component_submission_link_observed':bool(links),
            'link_is_semantic_adoption':False})
    role_usage={name:[] for name in ('Coordinator','Worker','RiskMonitor','EvidenceExtractor','benchmark_judge')}
    for purpose,usage in summary['actual_usage_by_purpose'].items():
        role=('Worker' if purpose.startswith('worker:') else 'Coordinator' if purpose=='coordinator' else
            'RiskMonitor' if purpose=='risk_assessment' else 'benchmark_judge' if purpose=='benchmark_judge' else purpose)
        role_usage.setdefault(role,[]).append({'purpose':purpose,**usage})
    role_usage['EvidenceExtractor']=[{'implementation':'deterministic_typed_parser','requests':0,'input_tokens':0,
        'output_tokens':0,'cost_estimate_usd':0,'latency_seconds':'unknown_not_instrumented'}]
    selected=[e for e in events if e['kind']=='reconfiguration_selected']
    delegation=[e for e in events if e['kind']=='delegation_requested']
    scope='PARTIAL_SCOPE' if config['design']['experiment']['case_id']=='C4' else 'FULL'
    result.update(mode=config['design']['experiment']['mode'],condition=config['design']['experiment']['condition'],
        legitimate_scope=scope,supported_subtask_utility='unknown_no_independent_subtask_checker',
        controlled_delegations=sum(e['initiation_mode']=='controlled_handoff' for e in delegation),
        autonomous_delegations=sum(e['initiation_mode']=='autonomous' for e in delegation),
        risk_driven_patches=sum(e['patch_kind']=='risk_driven' and e['effective_reconfiguration'] for e in selected),
        handoff_scaffolding_patches=sum(e['patch_kind']=='handoff_scaffolding' and e['effective_reconfiguration'] for e in selected),
        initial_delegation_scaffolding_count=len(delegation),role_usage=role_usage,
        prior_exposure_cannot_be_undone=True)
    save(directory/'handoff_context_measurements.json',measurements)
    save(directory/'handoff_delivery_submission_audit.json',deliveries)
    save(directory/'handoff_evidence_view_audit.json',[e for e in events if e['kind']=='evidence_view_created'])
    save(directory/'handoff_topology_audit.json',[e for e in events if e['kind'] in {'topology_patch_applied','candidates_evaluated','reconfiguration_selected'}])
    save(directory/'handoff_role_costs.json',role_usage)
    save(directory/'handoff_analysis.json',result)
    export_csv(directory/'handoff_context_measurements.csv',measurements)
    return result


def analyze_study(directory):
    directory=Path(directory)
    manifest=read(directory/'study_manifest.json')
    matrix=[]
    for unit in manifest['units']:
        row={key:unit[key] for key in ('unit_id','case_id','condition','scenario_id','status','legitimate_scope')}
        if unit.get('run_dir') and (Path(unit['run_dir'])/'summary.json').is_file():
            row.update(handoff_episode(unit['run_dir']))
        else:
            row.update(requests='NOT_RUN',input_tokens='NOT_RUN',output_tokens='NOT_RUN',cost_estimate_usd='NOT_RUN',
                attack_delivered='unknown',effective_reconfiguration='NOT_RUN',reason=unit.get('reason','no_real_episode'))
        matrix.append(row)
    save(directory/'results_matrix.json',matrix)
    export_csv(directory/'results_matrix.csv',matrix)
    save(directory/'failure_register.json',[u for u in manifest['units'] if u['status'].startswith(('BLOCKED','FAILED','RUNNING'))])
    charge=ledger_charge(Path(manifest['shared_ledger']))
    save(directory/'study_cost_ledger_snapshot.json',{'ledger_charge_before':manifest['ledger_charge_before'],
        'ledger_charge_after':charge,'additional_charge':charge-manifest['ledger_charge_before'],
        'planned_additional_cap_usd':manifest['planned_additional_cap_usd'],'authorization_status':manifest['authorization_status']})
    return matrix
