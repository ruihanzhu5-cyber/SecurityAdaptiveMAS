"""v0.4 executable contracts. Model proposals are never authority or facts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path

import yaml


class UnsupportedDesign(ValueError):
    pass


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{label}: expected keys {sorted(keys)}")


def strings(value, label):
    if not isinstance(value, (list, tuple)) or any(not isinstance(x, str) or not x for x in value):
        raise ValueError(f"{label}: expected nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label}: duplicate entries")
    return tuple(value)


@dataclass(frozen=True)
class GoalContract:
    revision: int
    text: str
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_observation(cls, observation):
        users = observation.user_events
        return cls(len(users), observation.task_description or "", tuple(e.evidence_id for e in users))


@dataclass(frozen=True)
class RoleContract:
    role_id: str
    task_id: str
    objective: str
    procedure: tuple[str, ...]
    required_inputs: tuple[str, ...]
    requested_tools: tuple[str, ...]
    output_contract: tuple[str, ...]
    completion_contract: dict
    lifecycle: str
    step_budget: int
    source_evidence_ids: tuple[str, ...]

    @classmethod
    def parse(cls, row, *, tools, evidence_ids, task_ids, max_steps):
        exact_keys(row, cls.__dataclass_fields__, "RoleContract")
        for key in ("role_id", "task_id", "objective"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f"Invalid role {key}")
        arrays = {key: strings(row[key], key) for key in (
            "procedure", "required_inputs", "requested_tools", "output_contract", "source_evidence_ids")}
        if not arrays["procedure"] or not arrays["output_contract"] or not arrays["source_evidence_ids"]:
            raise ValueError("Role requires procedure, output schema and evidence")
        if not set(arrays["requested_tools"]) <= set(tools):
            raise ValueError("role_requested_unavailable_capability")
        if not set((*arrays["required_inputs"], *arrays["source_evidence_ids"])) <= set(evidence_ids):
            raise ValueError("role_cites_unseen_evidence")
        if row["task_id"] not in task_ids or row["task_id"] == "root":
            raise ValueError("role_requires_registered_working_task")
        if type(row["step_budget"]) is not int or not 1 <= row["step_budget"] <= max_steps:
            raise ValueError("role_step_budget_outside_configuration")
        if row["lifecycle"] not in {"reuse", "retire_after_output", "suspend_after_output"}:
            raise ValueError("unsupported_role_lifecycle")
        exact_keys(row["completion_contract"], {"kind", "required_fields", "require_evidence"}, "completion")
        completion = row["completion_contract"]
        if completion["kind"] != "artifact_schema" or type(completion["require_evidence"]) is not bool:
            raise ValueError("UNSUPPORTED_completion_checker")
        fields = strings(completion["required_fields"], "required_fields")
        if not set(fields) <= set(arrays["output_contract"]):
            raise ValueError("completion_fields_not_in_output_contract")
        return cls(**{**row, **arrays})

    def check_output(self, text, visible_ids):
        data = json.loads(text)
        exact_keys(data, {"fields", "evidence_ids", "uncertainties"}, "role_output")
        exact_keys(data["fields"], self.output_contract, "role_output_fields")
        citations = strings(data["evidence_ids"], "output_evidence")
        if not set(citations) <= set(visible_ids):
            raise ValueError("output_cites_unseen_evidence")
        if self.completion_contract["require_evidence"] and not citations:
            raise ValueError("output_requires_observed_evidence")
        if any(data["fields"][key] in (None, "", [], {}) for key in self.completion_contract["required_fields"]):
            raise ValueError("output_missing_required_field")
        if not isinstance(data["uncertainties"], list):
            raise ValueError("output_uncertainties_requires_list")
        return data


@dataclass(frozen=True)
class NeedContract:
    need_id: str
    consumer_task_id: str
    origin: str
    evidence_ids: tuple[str, ...]
    provider_alternatives: tuple[dict, ...]
    acceptance_contract: dict
    status: str = "unknown"
    status_evidence_ids: tuple[str, ...] = ()

    @classmethod
    def parse(cls, row, task_ids, evidence_ids):
        exact_keys(row, {"need_id", "consumer_task_id", "origin", "evidence_ids",
                         "provider_alternatives", "acceptance_contract"}, "NeedContract")
        if row["origin"] != "proposed_plan" or row["consumer_task_id"] not in task_ids:
            raise ValueError("model_need_cannot_claim_trusted_origin")
        evidence = strings(row["evidence_ids"], "need_evidence")
        if not evidence or not set(evidence) <= set(evidence_ids):
            raise ValueError("need_requires_visible_evidence")
        for provider in row["provider_alternatives"]:
            exact_keys(provider, {"task_id", "output_slot"}, "provider")
            if provider["task_id"] not in task_ids or not provider["output_slot"]:
                raise ValueError("provider_requires_registered_task_and_slot")
        exact_keys(row["acceptance_contract"], {"required_fields", "independent_from"}, "need_acceptance")
        strings(row["acceptance_contract"]["required_fields"], "need_fields")
        roots = strings(row["acceptance_contract"]["independent_from"], "independent_from")
        if not set(roots) <= set(evidence_ids):
            raise ValueError("independence_cites_unseen_source")
        return cls(**{**row, "evidence_ids": evidence,
                      "provider_alternatives": tuple(row["provider_alternatives"])})


@dataclass(frozen=True)
class Activation:
    activation_id: str
    instance_id: str
    task_id: str
    round_index: int
    predecessors: tuple[str, ...]


@dataclass(frozen=True)
class RewritePlan:
    plan_id: str
    base_graph_version: int
    base_observation_sequence: int
    base_task_revision: int
    role_specs: tuple[tuple[str, RoleContract], ...]
    assignments: tuple[tuple[str, str], ...]
    topology_spec: dict
    protocol_spec: dict
    immediate_operations: tuple[dict, ...]
    existing_object_deliveries: tuple[tuple[str, str], ...]
    pending_work: tuple[dict, ...]
    future_output_bindings: tuple[dict, ...]
    release_conditions: tuple[str, ...]
    reason_evidence_ids: tuple[str, ...]
    activations: tuple[Activation, ...] = ()
    intervention: str = "task"
    cost_assessment: dict = field(default_factory=dict)
    risk_assessment: dict = field(default_factory=dict)

    def dedup_key(self):
        data = asdict(self)
        for key in ("plan_id", "intervention", "cost_assessment", "risk_assessment"):
            data.pop(key)
        data["topology_spec"] = {"edges":sorted(self.topology_spec["edges"])}
        activation_positions = {a.activation_id:index for index,a in enumerate(self.activations)}
        data["activations"] = [{"instance_id":a.instance_id,"task_id":a.task_id,"round":a.round_index,
                                "predecessors":[activation_positions[p] for p in a.predecessors]}
                               for a in self.activations]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def load_design(path: Path):
    design = yaml.safe_load(path.read_text(encoding="utf-8"))
    return load_design_value(design)


def load_design_value(design):
    required = {"version", "method", "max_agents", "max_candidates", "search_depth", "topologies",
                "topology_parameters", "protocol", "rounds", "role_step_limit", "reconfiguration_limit",
                "cost_mode", "calibration_file", "scheduling", "capability_mode", "delegable_tools",
                "risk_detector", "risk_tolerance", "seed", "subbudgets", "rates", "data_manifest",
                "attack_protocol", "attack_attempt_budgets", "ablations"}
    exact_keys(design, required, "research_design")
    if design["version"] != "v0.4" or design["method"] not in {
            "joint", "task_only", "fixed", "fixed_security", "reactive_isolation", "task_plus_filter", "single"}:
        raise ValueError("invalid_design_version_or_method")
    if not 1 <= design["max_agents"] <= 8 or not 1 <= design["max_candidates"] <= 24 or design["search_depth"] not in (1, 2):
        raise ValueError("design_resource_limits")
    if design["protocol"] not in {"dependency_schedule", "one_round_exchange", "bounded_review"} or not 1 <= design["rounds"] <= 8:
        raise ValueError("invalid_protocol_or_round_limit")
    if design["scheduling"] != "sequential":
        raise UnsupportedDesign("BLOCKED concurrent_reads: ARE app/notification atomicity is not established")
    if design["capability_mode"] not in {"coordinator_only", "delegated_scoped"}:
        raise ValueError("invalid_capability_mode")
    if design["capability_mode"] == "delegated_scoped" and not design["delegable_tools"]:
        raise ValueError("delegated_scoped_requires_trusted_capability_list")
    if design["risk_detector"] not in {"semantic", "regex"} or design["risk_tolerance"] != "non_increasing_vs_keep":
        raise ValueError("unsupported_risk_configuration")
    if design["cost_mode"] not in {"declared_work", "empirical"}:
        raise ValueError("invalid_cost_mode")
    if design["cost_mode"] == "empirical" and not design["calibration_file"]:
        raise ValueError("BLOCKED empirical_requires_development_calibration")
    if design["cost_mode"] == "empirical" and not Path(design["calibration_file"]).is_file():
        raise FileNotFoundError("BLOCKED empirical_calibration_file_missing")
    if not 1 <= design["role_step_limit"] <= 6 or not 1 <= design["reconfiguration_limit"] <= 12:
        raise ValueError("invalid_role_or_reconfiguration_limit")
    exact_keys(design["ablations"],{"dynamic_roles","dynamic_topology","repair","compact_inputs",
                                   "ordinary_replanning_same_time"},"ablations")
    if any(type(value) is not bool for value in design["ablations"].values()):
        raise ValueError("ablation_flags_require_boolean")
    if design["ablations"]["ordinary_replanning_same_time"]:
        raise UnsupportedDesign("BLOCKED same-time intervention requires full environment state restoration")
    from .topology_candidates import TOPOLOGY_FAMILIES
    if not design["topologies"] or not set(design["topologies"]) <= set(TOPOLOGY_FAMILIES):
        raise ValueError("invalid_topology_families")
    if any(type(limit) is not int or not 1 <= limit <= 100 for limit in design["subbudgets"].values()):
        raise ValueError("invalid_model_subbudgets")
    from .config import ROOT
    if not (ROOT / design["data_manifest"]).is_file():
        raise FileNotFoundError("research_data_manifest_missing")
    if design["attack_protocol"] not in {"fixed_transfer", "blackbox"} or set(design["attack_attempt_budgets"]) != {1, 4, 16}:
        raise ValueError("invalid_attack_protocol")
    return design
