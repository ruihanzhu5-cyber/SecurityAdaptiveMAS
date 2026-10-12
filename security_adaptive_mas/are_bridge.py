"""ARE custom-builder bridge. Real writes fail closed until action provenance is wired."""
from __future__ import annotations

from types import MethodType

from .security import ToolDefinition


class GuardedARETool:
    """Preserve ARE tool description while routing execution through ToolGateway."""

    def __init__(self, original, controller, task_evidence_id):
        self.original = original
        self.controller = controller
        self.task_evidence_id = task_evidence_id
        self.name = original.name
        self.description = original.description
        self.inputs = original.inputs
        self.output_type = original.output_type
        app_tool = getattr(original, "app_tool", None)
        self.resource = getattr(app_tool, "app_name", "unknown")
        self.mode = "read" if getattr(app_tool, "write_operation", None) is False else "write"
        controller.tools.register(ToolDefinition(
            self.name, self.mode, frozenset({self.resource}), original))

    def __call__(self, *args, **kwargs):
        if args:
            raise PermissionError("ARE positional tool arguments require explicit adapter")
        result, _evidence = self.controller.tools.call(
            self.controller.spec, "agent_0", self.controller.spec.version,
            self.name, self.resource, kwargs, self.task_evidence_id)
        return result


def guard_are_toolset(controller, tools, task_evidence_id):
    """Only exact policy-listed read tools can pass this bridge."""
    return [GuardedARETool(tool, controller, task_evidence_id) for tool in tools]


def make_guarded_agent_builder(controller, task_evidence_id):
    """Use ARE's official AbstractAgentBuilder seam without editing ARE code.

    The current bridge is single-agent and read-only. It preserves ARE's own
    event loop, validator, and trace exporter. A multi-agent LLM scheduler and
    causal action provenance are needed before enabling stateful writes.
    """
    from are.simulation.agents.agent_builder import AgentBuilder

    class GuardedAgentBuilder(AgentBuilder):
        def build(self, agent_config, env=None, mock_responses=None):
            agent = super().build(agent_config, env, mock_responses)
            original_init_tools = agent.init_tools

            def guarded_init_tools(instance, scenario):
                original_init_tools(scenario)
                guarded = guard_are_toolset(controller, instance.tools, task_evidence_id)
                instance.tools = guarded
                instance.react_agent.tools = {tool.name: tool for tool in guarded}

            agent.init_tools = MethodType(guarded_init_tools, agent)
            return agent

    return GuardedAgentBuilder()
