"""ARE custom-builder bridge. Real writes fail closed until action provenance is wired."""
from __future__ import annotations

import json
import hashlib
import time
from types import MethodType

from .security import ToolDefinition


class ObservedContext:
    """Conservative provenance of inputs actually submitted to the model."""

    def __init__(self, controller, initial_evidence_id: str, agent_id: str = "agent_0"):
        self.controller = controller
        self.agent_id = agent_id
        self.visible_ids = [initial_evidence_id]
        self.current_evidence_id = initial_evidence_id
        self.last_messages = []
        self._next_tool_requirements = ()

    def bind_next_tool_requirements(self, keys: tuple[tuple[str, str], ...]) -> None:
        """Trusted serial scheduler only; never exposed as an ARE/LLM tool."""
        self._next_tool_requirements = ()
        self._next_tool_requirements = self.controller.validate_related_requirements(keys)

    def consume_tool_requirements(self) -> tuple[tuple[str, str], ...]:
        """Consume before execution, including when validation or the tool fails."""
        bound = self._next_tool_requirements
        self._next_tool_requirements = ()
        if not bound:
            return ()
        keys = tuple((item.task_id, item.requirement_id) for item in bound)
        if self.controller.validate_related_requirements(keys) != bound:
            raise ValueError("stale_information_requirement_binding")
        return keys

    def record_delivered_notification(self, kind: str, message) -> None:
        # Called only after ARE's timestamp-gated get_notifications returns.
        # Never inspect the Scenario or the notification queue's future items.
        text = message.message
        evidence = self.controller.provenance.add(
            f"are:{kind}_notification", "trusted_task" if kind == "user" else "low_trust", text)
        timestamp = getattr(message, "timestamp", None)
        event = self.controller.online_observations.record_notification(
            kind, text, evidence.evidence_id, agent_instance_id=self.agent_id,
            status="observed",
            simulation_time=timestamp.timestamp() if timestamp is not None else None)
        self.controller.on_observed_event(event)
        self.visible_ids.append(evidence.evidence_id)
        self.current_evidence_id = evidence.evidence_id

    def record_model_input(self, messages):
        self.last_messages = messages
        text = json.dumps(messages, ensure_ascii=False, default=str)
        evidence = self.controller.provenance.add(
            "runtime:model_input", "derived", text,
            parents=tuple(dict.fromkeys(self.visible_ids)), owner_agent=self.agent_id)
        self.current_evidence_id = evidence.evidence_id
        self.visible_ids.append(evidence.evidence_id)
        self.controller.trace.log("model_input_observed", agent_id=self.agent_id,
                                  evidence_id=evidence.evidence_id,
                                  parent_ids=evidence.parent_ids)
        if self.controller.monitor_enabled:
            event = self.controller.online_observations.record_model_input(
                evidence.evidence_id, hashlib.sha256(text.encode("utf-8")).hexdigest(),
                len(text), agent_instance_id=self.agent_id)
            self.controller.on_observed_event(event)

    def record_tool_output(self, evidence_id: str):
        self.visible_ids.append(evidence_id)


class GuardedARETool:
    """Preserve ARE tool description while routing execution through ToolGateway."""

    def __init__(self, original, controller, context):
        self.original = original
        self.controller = controller
        self.context = context
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
        related_requirements = (self.context.consume_tool_requirements()
                                if isinstance(self.context, ObservedContext) else ())
        if args:
            raise PermissionError("ARE positional tool arguments require explicit adapter")
        evidence_id = (self.context.current_evidence_id
                       if isinstance(self.context, ObservedContext) else self.context)
        started = time.monotonic()
        result, evidence = self.controller.tools.call(
            self.controller.spec, "agent_0", self.controller.spec.version,
            self.name, self.resource, kwargs, evidence_id)
        if isinstance(self.context, ObservedContext):
            elapsed = time.monotonic() - started
            action = self.controller.online_observations.record_action(
                self.name, evidence_id, agent_instance_id=self.context.agent_id,
                status="completed", elapsed_seconds=elapsed)
            output = self.controller.online_observations.record_tool_result(
                self.name, evidence.evidence_id, agent_instance_id=self.context.agent_id,
                status="completed", elapsed_seconds=elapsed)
            self.controller.on_observed_event(action)
            self.controller.on_observed_event(output, related_requirements=related_requirements)
            self.context.record_tool_output(evidence.evidence_id)
        return result


def guard_are_toolset(controller, tools, context):
    """Only exact policy-listed read tools can pass this bridge."""
    return [GuardedARETool(tool, controller, context) for tool in tools]


def record_are_delivery(context: ObservedContext, users, environment) -> None:
    """Map only timestamp-delivered ARE notifications into the online view."""
    for message in users:
        context.record_delivered_notification("user", message)
    for message in environment:
        context.record_delivered_notification("environment", message)


def make_guarded_agent_builder(controller, context, llm_engine_builder=None,
                               allow_read_apps: frozenset[str] = frozenset(),
                               allow_read_tools: frozenset[str] = frozenset(),
                               dynamic_client=None):
    """Use ARE's official AbstractAgentBuilder seam without editing ARE code.

    The current bridge is single-agent and read-only. It preserves ARE's own
    event loop, validator, and trace exporter. A multi-agent LLM scheduler and
    causal action provenance are needed before enabling stateful writes.
    """
    from are.simulation.agents.agent_builder import AgentBuilder

    class GuardedAgentBuilder(AgentBuilder):
        def build(self, agent_config, env=None, mock_responses=None):
            agent = super().build(agent_config, env, mock_responses)
            original_get_notifications = agent.get_notifications

            def observed_get_notifications(instance, notification_system):
                users, environment, stops = original_get_notifications(notification_system)
                record_are_delivery(context, users, environment)
                return users, environment, stops

            agent.get_notifications = MethodType(observed_get_notifications, agent)
            original_init_tools = agent.init_tools

            def guarded_init_tools(instance, scenario):
                original_init_tools(scenario)
                guarded = guard_are_toolset(controller, instance.tools, context)
                enabled = [tool for tool in guarded if tool.mode == "read" and
                           tool.resource in allow_read_apps and
                           (not allow_read_tools or tool.name in allow_read_tools)]
                controller.authorized_tools = frozenset(tool.name for tool in enabled)
                controller.tools.authorized_tools = controller.authorized_tools
                controller.tools.authorized_resources = allow_read_apps
                controller.update("ARE task delivered through official notifications", (), "intake")
                controller.trace.log("are_read_tools_enabled",
                                     tools=sorted(controller.authorized_tools),
                                     apps=sorted(allow_read_apps))
                if dynamic_client is not None:
                    from .dynamic_team import make_dynamic_team_tool
                    enabled.append(make_dynamic_team_tool(controller, context, dynamic_client,
                                                          enabled))
                instance.tools = enabled
                instance.react_agent.tools = {tool.name: tool for tool in enabled}

            agent.init_tools = MethodType(guarded_init_tools, agent)
            return agent

    return GuardedAgentBuilder(llm_engine_builder=llm_engine_builder)
