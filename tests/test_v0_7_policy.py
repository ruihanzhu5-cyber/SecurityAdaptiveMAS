import pytest

from security_adaptive_mas.orchestration.policy import ReconfigurationPolicy
from security_adaptive_mas.orchestration.evidence_views import EvidenceViewBuilder
from test_v0_6_modular import make_session as make_session,delegate,finish,get,events,response


@pytest.mark.parametrize('condition,removed',[('attacked_graph_only',True),('attacked_joint',True),('attacked_context_only',False)])
def test_hold_revokes_only_existing_path_under_graph_control(make_session,condition,removed):
    s,_,_=make_session([finish('INJECT')])
    receipt=delegate(s)
    ref=s.resolve('agent_0',receipt['artifact_handles'][0]['object_id'],'full')
    s.runtime.apply(s.patch([{'op':'ADD_ROUTE','source':'worker_0001','target':'agent_0'},
        {'op':'SET_OBJECT_ACCESS','agent_id':'agent_0','object_id':ref.key,'allowed':True}]))
    s.policy=ReconfigurationPolicy(s.config['method'],condition)
    s.evidence_builder=EvidenceViewBuilder(s,('date_start',))
    out=get(s,ref.object_id,fields=['date_start'])
    assert out['status']=='held' and 'INJECT' not in str(out)
    assert (('worker_0001','agent_0') not in s.runtime.graph.extra_edges)==removed
    assert (('agent_0',ref.key) not in s.runtime.graph.object_access)==removed
    selected=next(e for e in reversed(events(s)) if e['kind']=='reconfiguration_selected')
    assert selected['effective_reconfiguration']==removed
    assert selected['patch_kind']==('risk_driven' if removed else 'handoff_scaffolding')


def test_worker_finish_preserves_non_full_parent_view(make_session):
    read={'action':'Emails__list_emails','params':{'folder_name':'INBOX'},'memory':'read real typed fixture'}
    s,_,_=make_session([read,finish()])
    s.evidence_builder=EvidenceViewBuilder(s,('timestamp',))
    s.policy=ReconfigurationPolicy(s.config['method'],'attacked_context_only')
    receipt=delegate(s,tools=['Emails__list_emails'],fields=['timestamp'])
    original=s.resolve('agent_0',receipt['artifact_handles'][0]['object_id'],'full')
    view=s.views[(original.key,'task_evidence_v1')]
    prior=s.client.completion_fn
    actions=[{'action':'get_artifact','params':{'artifact_id':view.object_id,'view':'task_evidence_v1',
        'for_work_id':'work_0002','required_fields':['timestamp']},'memory':'consume view'},finish()]
    def complete(**kwargs):
        return prior(**kwargs) if kwargs['messages'][0]['role']=='system' else response(actions.pop(0))
    s.client.completion_fn=complete
    second=delegate(s,inputs=[original.object_id],fields=['timestamp'])
    derived=s.resolve('agent_0',second['artifact_handles'][0]['object_id'],'full')
    assert view in s.artifact_records[derived.key].parent_refs
    assert not s.evidence_builder.ledger[s.views[(derived.key,'task_evidence_v1')].key].missing_required_fields


def test_empty_request_cannot_release_incomplete_view_and_stale_graph_rejected(make_session):
    from security_adaptive_mas.orchestration.contracts import DeliveryRequest
    s,_,_=make_session([finish('Unanchored worker date claim')])
    s.evidence_builder=EvidenceViewBuilder(s,('date_start','date_end'))
    s.policy=ReconfigurationPolicy(s.config['method'],'attacked_context_only')
    receipt=delegate(s)
    ref=s.resolve('agent_0',receipt['artifact_handles'][0]['object_id'],'full')
    assert get(s,ref.object_id,fields=[])['status']=='held'
    stale=DeliveryRequest('stale','agent_0','root',ref,(),s.runtime.graph.version-1)
    with pytest.raises(ValueError,match='stale_v0_7_delivery_request_graph_version'):
        s.delivery_service.request(stale)
