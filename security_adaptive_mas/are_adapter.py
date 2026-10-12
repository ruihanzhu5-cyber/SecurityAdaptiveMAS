"""Narrow ARE integration points; importing this module does not import ARE."""
from __future__ import annotations

import sys


def import_official_scenario(record, are_root):
    """Use ARE's own JSON importer; never inspect future events in controller."""
    if str(are_root) not in sys.path:
        sys.path.insert(0, str(are_root))
    from are.simulation.data_handler.importer import JsonScenarioImporter

    scenario, _completed, _logs = JsonScenarioImporter().import_from_json(
        record.path.read_bytes(), load_completed_events=False
    )
    if scenario.scenario_id != record.scenario_id:
        raise ValueError("Official importer scenario ID mismatch")
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
