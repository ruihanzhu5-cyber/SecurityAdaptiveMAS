from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ScenarioRecord:
    scenario_id: str
    path: Path
    single_scenario_dir: Path
    sha256: str


@dataclass(frozen=True)
class ProjectConfig:
    scenario_ids: tuple[str, ...]
    records: dict[str, ScenarioRecord]
    manta_root: Path
    are_root: Path
    max_agents: int
    parallel_threshold: int
    weights: dict[str, float]

    def select(self, scenario_id: str) -> ScenarioRecord:
        if scenario_id not in self.scenario_ids:
            raise ValueError(f"Scenario not in fixed v1 set: {scenario_id}")
        record = self.records[scenario_id]
        if hashlib.sha256(record.path.read_bytes()).hexdigest() != record.sha256:
            raise ValueError(f"Scenario checksum mismatch: {scenario_id}")
        return record


def load_config(path: Path = ROOT / "configs" / "gaia2_v1_five.yaml") -> ProjectConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = tuple(raw["scenarios"])
    if len(ids) != 5 or len(set(ids)) != 5:
        raise ValueError("v1 requires exactly five distinct scenarios")
    manifest_path = ROOT / raw["source"]["manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = {}
    for row in manifest:
        relative_json = Path(row["path"])
        relative_single = Path(row["single_scenario_dir"])
        if relative_json.is_absolute() or relative_single.is_absolute():
            raise ValueError("Scenario manifest paths must be project-relative")
        records[row["scenario_id"]] = ScenarioRecord(
            row["scenario_id"],
            ROOT / relative_json,
            ROOT / relative_single,
            row["sha256"],
        )
    if set(records) != set(ids):
        raise ValueError("Fixed scenario list and manifest differ")
    controller = raw["controller"]
    return ProjectConfig(
        ids, records, ROOT / raw["source"]["manta_root"],
        ROOT / raw["source"]["are_root"],
        int(controller["max_agents"]), int(controller["parallel_subtasks_threshold"]),
        {key: float(value) for key, value in controller["score_weights"].items()},
    )
