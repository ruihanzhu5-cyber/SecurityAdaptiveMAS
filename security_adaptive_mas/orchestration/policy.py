"""Deterministic local selection. Cost proxies are not predicted Token savings."""


class ReconfigurationPolicy:
    def __init__(self, method, condition=None):
        self.method=method
        self.condition=condition

    def select(self, candidates):
        feasible=[]
        for candidate in candidates:
            if candidate.status!="validated":continue
            risk=candidate.risk_projection
            if self.condition is not None:
                baseline=self.condition in {'clean_full','attacked_full'}
                if candidate.action=='FULL_RELEASE' and not baseline and risk.get('protected_receiver') and risk.get('suspicion')!='not_supported':
                    candidate.status='rejected'
                    candidate.rejection_reason='unassessed_or_suspect_full_payload_to_protected_receiver'
                    continue
                if candidate.action=='EVIDENCE_VIEW_RELEASE' and (risk['missing_fields'] or risk['contains_attack_like_content'] is not False):
                    candidate.status='rejected'
                    candidate.rejection_reason='incomplete_or_unbounded_evidence_view'
                    continue
            elif self.method=="modular_risk_aware" and candidate.action in {"RELEASE","LIMITED_RELEASE"}:
                if risk.get("protected_receiver") and (risk.get("suspicion")=="unknown" or
                    (risk.get("suspicion")=="supported" and risk.get("carries_suspect_payload",True))):
                    candidate.status="rejected"
                    candidate.rejection_reason="new_suspect_or_unassessed_payload_to_protected_role"
                    continue
            feasible.append(candidate)
        if not feasible:
            raise RuntimeError("no_structurally_valid_candidate_including_HOLD")
        def order(c):
            rank=0 if c.action in {"RELEASE","LIMITED_RELEASE","FULL_RELEASE","EVIDENCE_VIEW_RELEASE"} else 1 if c.action in {"REQUERY","REASSIGN"} else 2
            return (rank,c.cost_proxy.get("model_steps_upper_bound",0),
                    c.cost_proxy.get("delivery_bytes",0),c.cost_proxy.get("new_instance",False),c.candidate_id)
        selected=min(feasible,key=order)
        selected.status="selected"
        return selected
