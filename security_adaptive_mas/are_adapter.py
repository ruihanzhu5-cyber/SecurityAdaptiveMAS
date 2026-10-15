"""Narrow ARE integration points; importing this module does not import ARE."""
from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path

from .config import ROOT


def prepare_are_sandbox(*, filesystem=None, sandbox=None):
    """Keep ARE's temporary filesystem inside the D-drive project."""
    sandbox = ROOT / "work" / "are_tmp" if sandbox is None else Path(sandbox)
    filesystem = ROOT / "work" / "gaia2_filesystem" / "demo_filesystem" if filesystem is None else Path(filesystem)
    if not filesystem.is_dir():
        raise FileNotFoundError(f"Pinned Gaia2 filesystem missing: {filesystem}")
    sandbox.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = str(sandbox)
    os.environ["TEMP"] = str(sandbox)
    tempfile.tempdir = str(sandbox)
    os.environ["DEMO_FS_PATH"] = str(filesystem)


def import_official_scenario(record, are_root, *, filesystem=None, sandbox=None):
    """Use ARE's own JSON importer; never inspect future events in controller."""
    if str(are_root) not in sys.path:
        sys.path.insert(0, str(are_root))
    prepare_are_sandbox(filesystem=filesystem, sandbox=sandbox)
    from are.simulation.data_handler.importer import JsonScenarioImporter

    scenario, _completed, _logs = JsonScenarioImporter().import_from_json(
        record.path.read_bytes(), load_completed_events=False
    )
    if scenario.scenario_id != record.scenario_id:
        raise ValueError("Official importer scenario ID mismatch")
    scenario.initialize(sandbox_dir=str(sandbox or ROOT / "work" / "are_tmp"))
    return scenario


def official_runner(agent_builder=None):
    """Construct ARE runner with its validator and trace export intact."""
    from are.simulation.scenario_runner import ScenarioRunner

    return ScenarioRunner(agent_builder=agent_builder)


def inspect_tool_metadata(scenario):
    """Return ARE app tool metadata; unknown write status remains unsafe."""
    return [
        {"name": tool._public_name or tool.name, "app": tool.app_name,
         "write_operation": tool.write_operation}
        for tool in scenario.get_tools()
    ]
