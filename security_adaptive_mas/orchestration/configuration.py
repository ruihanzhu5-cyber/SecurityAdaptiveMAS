"""v0.6-only configuration, with explicit ignored local path overrides."""
import hashlib
import json
from pathlib import Path
import yaml
from ..config import ROOT, ProjectConfig, ScenarioRecord


def load_design(path, override=None):
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if override is None else override
    if value.get("version") != "v0.6" or value.get("runtime_kind") != "modular":
        raise ValueError("Use the v0.5 worktree for legacy design configurations")
    if value["method"] not in {"modular_task_only", "modular_risk_aware"}:
        raise ValueError("invalid_v0_6_method")
    fixed = {"model":"deepseek-flash", "scheduling":"sequential", "coordinator":"are_default",
             "worker_backend":"aorchestra_react_fork", "risk_detector":"semantic",
             "capability_mode":"coordinator_write_workers_read"}
    for key, expected in fixed.items():
        if value.get(key) != expected:
            raise ValueError(f"unsupported_v0_6_setting:{key}")
    for key, cap in {"max_active_agents":4, "max_total_instances":8, "worker_max_steps":8,
                     "max_candidates":6, "max_auto_repairs_per_work":1, "max_reconfigurations":24}.items():
        if type(value.get(key)) is not int or not 1 <= value[key] <= cap:
            raise ValueError(f"invalid_v0_6_limit:{key}")
    context=value["context"]
    if context != {"manta_latest_only":False,"manta_summary_only":False,
                   "manta_evidence_access":"own","deterministic_views":["email_metadata_v1"]}:
        raise ValueError("unsupported_context_policy")
    local=ROOT/"work/v0_6_local_paths.yaml"
    value={**value,"paths":dict(value["paths"])}
    if local.exists():
        paths=yaml.safe_load(local.read_text(encoding="utf-8"))
        if set(paths)-set(value["paths"]):
            raise ValueError("unknown_local_path")
        value["paths"].update(paths)
    value["paths"]={k:str((ROOT/Path(v)).resolve()) if v is not None else None for k,v in value["paths"].items()}
    value["upstream_lock"]=str((ROOT/Path(value["upstream_lock"])).resolve())
    if not Path(value["paths"]["sandbox_root"]).is_relative_to(ROOT):
        raise ValueError("sandbox_must_be_in_new_worktree")
    return value


def project_config(design):
    for name in ("atm_root","are_root","gaia2_filesystem","scenario_root"):
        if design["paths"].get(name) is None:
            raise FileNotFoundError(f"BLOCKED_explicit_path_missing:{name}")
    source=Path(design["paths"]["scenario_root"])
    manifest=json.loads((ROOT/"configs/five_manifest.json").read_text(encoding="utf-8"))
    records={row["scenario_id"]: ScenarioRecord(row["scenario_id"], source/row["path"],
        source/row["single_scenario_dir"], row["sha256"]) for row in manifest}
    return ProjectConfig(tuple(records),records,ROOT/"security_adaptive_mas/_vendor/manta",
                         Path(design["paths"]["are_root"]),design["max_active_agents"],0,{})


def design_hash(design):
    return hashlib.sha256(json.dumps(design,sort_keys=True).encode()).hexdigest()
