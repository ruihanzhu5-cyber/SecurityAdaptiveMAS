from dataclasses import dataclass
from uuid import uuid4

import pytest

from security_adaptive_mas.are_bridge import guard_are_toolset
from security_adaptive_mas.config import ROOT, load_config
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.trace import TraceLogger


@dataclass
class FakeAppTool:
    app_name: str
    write_operation: bool | None


class FakeARETool:
    def __init__(self, name, mode, calls):
        self.name, self.description, self.inputs, self.output_type = name, name, {}, "any"
        self.app_tool = FakeAppTool("MockApp", mode)
        self.calls = calls

    def __call__(self, **kwargs):
        self.calls.append((self.name, kwargs))
        return "ok"


def test_official_tool_boundary_read_only():
    calls = []
    controller = Controller(
        load_config(), TraceLogger(ROOT / "work" / "test_logs" / f"{uuid4().hex}.jsonl"),
        frozenset({"read"}), frozenset({"MockApp"}))
    task = controller.provenance.add("user", "trusted_task", "read this")
    controller.update(task.text, (), "intake")
    guarded = guard_are_toolset(controller, [FakeARETool("read", False, calls),
                                             FakeARETool("write", True, calls),
                                             FakeARETool("unknown", None, calls)],
                               task.evidence_id)
    assert guarded[0]() == "ok"
    with pytest.raises(PermissionError, match="tool_permission"):
        guarded[1]()
    with pytest.raises(PermissionError, match="tool_permission"):
        guarded[2]()
    assert calls == [("read", {})]
