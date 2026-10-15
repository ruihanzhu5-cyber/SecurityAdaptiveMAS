"""Pinned ATM calls plus explicit empty-scope/deny/identity checks."""
from collections import Counter
from dataclasses import asdict
import hashlib
import importlib
import json
from pathlib import Path
import sys
from ..config import ROOT


class ATMMutationAdapter:
    def __init__(self, source_root, lock, trace):
        self.root=Path(source_root).resolve()
        if self.root != Path(lock["atm"]["source_root"]).resolve():
            raise ValueError("atm_root_not_locked")
        for row in lock["atm"]["files"]:
            path=self.root/row["upstream_path"]
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row["original_sha256"]:
                raise ValueError("atm_source_hash_mismatch:"+str(path))
        sys.path.insert(0,str(self.root))
        self.cap=importlib.import_module("jiuwen_atm.safety.capability_policy")
        self.contract=importlib.import_module("jiuwen_atm.safety.mutation_contract")
        self.atoms=importlib.import_module("jiuwen_atm.topology.state_atoms")
        self.distiller=importlib.import_module("jiuwen_atm.topology.state_distiller")
        for module in (self.cap,self.contract,self.atoms,self.distiller):
            if not Path(module.__file__).resolve().is_relative_to(self.root):
                raise ValueError("atm_import_path_mismatch")
        self.trace=trace

    @staticmethod
    def verify_identity(inputs, outputs):
        if Counter(inputs)!=Counter(outputs) or any(n!=1 for n in Counter(inputs).values()):
            raise ValueError("state_identity_not_exactly_once")

    def validate(self, *, parent_id, parent_tools, parent_refs, denied_tools, children,
                 records, destinations, provenance):
        if parent_id is None:
            raise ValueError("unregistered_parent")
        checks=[]
        for child in children:
            checks.extend([set(child["tools"]) <= set(parent_tools)-set(denied_tools),
                           set(child["memory_scope"]) <= set(parent_refs),
                           not set(child["tools"]) & set(child.get("forbidden_tools",()))])
        if not all(checks):
            raise PermissionError("atm_explicit_capability_or_memory_check_failed")
        policy=self.cap.CapabilityPolicy()
        policy.register(self.cap.PolicyConfig(parent_id,set(parent_tools),set(denied_tools),set(parent_refs)))
        contract=self.contract.MutationContract(parent_agent=parent_id,mutation_reason=["explicit_runtime_decision"],
            children=[self.contract.ChildSpec(c["id"],list(c["tools"]),list(c["memory_scope"]),list(c.get("forbidden_tools",()))) for c in children])
        ok,reason=contract.verify_invariants(policy)
        if not ok:
            raise PermissionError("atm_contract_rejected:"+reason)
        child_ids={c["id"] for c in children}
        atoms=[]
        for record in records:
            ref=record.ref
            evidence=provenance.items[ref.object_id]
            atoms.append(self.atoms.StateAtom(atom_id=ref.key,
                type=self.atoms.AtomType.USER_GOAL if evidence.trust=="trusted_task" else self.atoms.AtomType.TOOL_RESULT,
                content=evidence.text,dependencies=[r.key for r in record.parent_refs],
                security_label=self.atoms.SecurityLabel.PUBLIC if evidence.trust=="trusted_task" else self.atoms.SecurityLabel.WEB_UNTRUSTED,
                metadata={"ref":asdict(ref),"source_call_id":record.source_call_id,"data_dependencies":record.data_dependencies}))
        if set(destinations)!={a.atom_id for a in atoms} or not set(destinations.values()) <= child_ids|{"_shared","_quarantine"}:
            raise ValueError("invalid_exact_state_destinations")
        Base=self.distiller.StateDistiller
        class ExactStateDistiller(Base):
            def _route(self, atom, children):
                if atom.atom_id not in destinations:
                    raise ValueError("state_destination_missing")
                return destinations[atom.atom_id]
        result=ExactStateDistiller(policy).distill(atoms,children)
        output=result.shared+result.quarantine+[a for values in result.per_role.values() for a in values]
        self.verify_identity([a.atom_id for a in atoms],[a.atom_id for a in output])
        actual={a.atom_id:"_shared" for a in result.shared}
        actual.update({a.atom_id:"_quarantine" for a in result.quarantine})
        actual.update({a.atom_id:child for child,values in result.per_role.items() for a in values})
        if actual!=destinations:
            raise ValueError("state_route_differs_from_candidate")
        row={"contract":asdict(contract),"atm_return":[ok,reason],"explicit_set_checks":checks,
             "identity_check":True,"destinations":actual,"structural_validation":"passed",
             "source_modules":[str(m.__file__) for m in (self.cap,self.contract,self.distiller)]}
        self.trace.log("mutation_validation",**row)
        return row
