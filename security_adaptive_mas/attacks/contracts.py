"""Immutable attack definitions and explicit per-opportunity results."""
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any

@dataclass(frozen=True)
class AttackSpec:
    attack_id: str
    surface: str              # e.g. "environment_notification"
    target_case: str          # scenario_id
    source_selector: str      # e.g. "first_environment_notification"
    payload_path: str
    payload_hash: str
    desired_deviation: str
    evaluation_rule: str
    protected_targets: tuple[str, ...] = ()
    target_email_id: str | None = None
    source_tool: str | None = None
    field_path: str | None = None
    receiver_policy: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    source_conversation_id: str | None = None
    evaluator_parameters_json: str = "{}"

    # Keep historical constructors and dataclasses.replace usable. New callers
    # use these canonical names; JSON normalization belongs to the Loader.
    @property
    def scenario_id(self):
        return self.target_case

    @property
    def surface_type(self):
        return self.surface

    @property
    def payload_sha256(self):
        return self.payload_hash

    @property
    def evaluator_id(self):
        return self.evaluation_rule

    @property
    def evaluator_params(self):
        params=json.loads(self.evaluator_parameters_json)
        params.update({k:getattr(self,k) for k in ('target_type','target_id','target_email_id')
                       if getattr(self,k) is not None})
        return MappingProxyType(params)


@dataclass(frozen=True)
class AttackContext:
    scenario_id: str
    caller_id: str
    tool_name: str
    call_id: str | None
    phase: str
    tool_result: Any
    simulation_time: float | None = None


@dataclass(frozen=True)
class AttackApplyResult:
    matched: bool
    applied: bool
    modified_result: Any
    reason: str
    attack_event_id: str | None = None
    original_text: str | None = None
    injected_text: str | None = None
