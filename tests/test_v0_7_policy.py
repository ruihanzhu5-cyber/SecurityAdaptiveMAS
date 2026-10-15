import pytest

from security_adaptive_mas.orchestration.policy import ReconfigurationPolicy
from security_adaptive_mas.orchestration.evidence_views import EvidenceViewBuilder
from test_v0_6_modular import make_session as make_session,delegate,finish,get,events


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
