"""Read-only local capability checks. No installs, downloads, or model calls."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

from ..api import load_configuration
from ..config import ROOT, load_config


def inspect(config_path):
    design = load_configuration(config_path)
    config = load_config()
    modules = {name: importlib.util.find_spec(name) is not None
               for name in ("yaml", "litellm", "pyarrow", "pytest", "deepdiff")}
    return {
        "python": sys.version.split()[0], "executable": sys.executable, "project_root": str(ROOT),
        "config": str(Path(config_path).resolve()), "method": design["method"],
        "modules": modules, "api_key_present": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "selected_data": {key: record.path.is_file() for key, record in config.records.items()},
        "data_manifest_present": (ROOT / design["data_manifest"]).is_file(),
        "gaia2_filesystem_present": (ROOT / "work/gaia2_filesystem/demo_filesystem").is_dir(),
        "are_source_present": (config.are_root / "are/simulation/scenario_runner.py").is_file(),
        "manta_topology_source_present": (config.manta_root / "MAS/self_evolved/spec.py").is_file(),
        "cost_mode": design["cost_mode"], "empirical_calibration": "UNVALIDATED" if design["calibration_file"] else "NOT_CALIBRATED",
        "agentdyn": {"status": "BLOCKED", "deepdiff_present": modules["deepdiff"],
                     "reason": "adapter and trusted tool side-effect catalog not validated"},
        "concurrent_reads": "BLOCKED", "complete_state_restore": "BLOCKED",
        "semantic_completion": "OPEN_no_independent_checker", "model_calls": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/research_v0_4.yaml")
    args = parser.parse_args(argv)
    result = inspect(args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result
