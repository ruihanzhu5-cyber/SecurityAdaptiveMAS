"""Compatibility imports for historical attack runners and reports."""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from .attacks.contracts import AttackSpec
from .attacks.loader import load_attack_spec
from .attacks.runtime import AttackOverlay
from .attacks.evaluators import (evaluate_attack, evaluate_text_attack, attack_target_state, _protected_tool_names)

__all__ = ['AttackSpec','AttackOverlay','load_attack_spec','evaluate_attack',
           'evaluate_text_attack','attack_target_state','_protected_tool_names','AttackCase']

@dataclass(frozen=True)
class AttackCase:
    case_id: str
    attack_family: str
    benchmark_id: str
    scenario_id: str
    surface: str
    field_selector: str
    editable_scope: str
    payload_or_generator_ref: str
    payload_hash: str
    attacker_observation_policy: str
    modification_budget: int
    success_evaluator_ref: str
    legitimate_task_evaluator_ref: str

    @classmethod
    def load(cls,path):
        from .organization_contracts import exact_keys
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        exact_keys(data,cls.__dataclass_fields__,"AttackCase")
        if data["benchmark_id"] != "gaia2":
            raise ValueError("BLOCKED_attack_case_benchmark_adapter_unavailable")
        if data["attack_family"] not in {"A","B","C","D","direct_command_control","benign_guidance","benign_error","legitimate_change"}:
            raise ValueError("unknown_attack_family")
        if data["surface"] not in {"tool_result_email_content","environment_notification"}:
            raise ValueError("UNSUPPORTED_unverified_attack_surface")
        if data["surface"] == "environment_notification" and data["field_selector"] != "first_environment_notification":
            raise ValueError("UNSUPPORTED_environment_selector")
        if data["surface"] == "tool_result_email_content" and not data["field_selector"].startswith("email_id:"):
            raise ValueError("UNSUPPORTED_email_selector")
        if not data["success_evaluator_ref"].startswith("email_trash:"):
            raise ValueError("BLOCKED_attack_case_requires_registered_independent_evaluator")
        if data["modification_budget"] != 1 or data["attacker_observation_policy"] not in {"success_only","declared_visible_trace"}:
            raise ValueError("unsupported_modification_or_feedback_policy")
        payload_path = Path(data["payload_or_generator_ref"])
        if not payload_path.is_absolute():
            payload_path = Path(path).parent/payload_path
        if hashlib.sha256(payload_path.read_bytes()).hexdigest() != data["payload_hash"]:
            raise ValueError("attack_payload_hash_mismatch")
        return cls(**{**data,"payload_or_generator_ref":str(payload_path)})

    def overlay_spec(self):
        # Evaluation references stay in the harness; no target truth enters a role prompt.
        return AttackSpec(self.case_id,self.surface,self.scenario_id,self.field_selector,
                          self.payload_or_generator_ref,self.payload_hash,"harness_only",
                          self.success_evaluator_ref,("tool:Emails__delete_email",),
                          self.success_evaluator_ref.removeprefix("email_trash:"))
