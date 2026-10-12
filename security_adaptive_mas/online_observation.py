"""The Controller's online view. Only runtime delivery callbacks may add facts.

This module deliberately has no dependency on an ARE Scenario, oracle, or judge.
The ARE adapter retains those objects for execution and offline evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Knowledge(str, Enum):
    UNKNOWN = "unknown"
    OBSERVED = "observed"


@dataclass(frozen=True)
class ObservedEvent:
    sequence: int
    kind: str  # user, environment, action, tool_result
    evidence_id: str
    text: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class WorkState:
    completed: tuple[str, ...] = ()
    running: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    knowledge: Knowledge = Knowledge.UNKNOWN


@dataclass(frozen=True)
class RuntimeBudget:
    requests_remaining: int | None = None
    tokens_remaining: int | None = None
    cost_remaining_usd: float | None = None
    knowledge: Knowledge = Knowledge.UNKNOWN


@dataclass(frozen=True)
class OnlineObservation:
    task_description: str | None
    sequence: int
    events: tuple[ObservedEvent, ...]
    topology_version: int | None
    work: WorkState
    budget: RuntimeBudget
    evidence_ids: tuple[str, ...]

    @property
    def user_events(self) -> tuple[ObservedEvent, ...]:
        return tuple(event for event in self.events if event.kind == "user")

    @property
    def environment_events(self) -> tuple[ObservedEvent, ...]:
        return tuple(event for event in self.events if event.kind == "environment")

    @property
    def actions(self) -> tuple[ObservedEvent, ...]:
        return tuple(event for event in self.events if event.kind == "action")

    @property
    def tool_results(self) -> tuple[ObservedEvent, ...]:
        return tuple(event for event in self.events if event.kind == "tool_result")


class OnlineObservationRecorder:
    """Append-only facts from messages already delivered and calls already made.

    The API accepts plain values and evidence IDs, never a scenario object. A
    caller must supply a provenance ID for each recorded runtime observation.
    """

    def __init__(self):
        self._events: list[ObservedEvent] = []
        self._task_description: str | None = None
        self._work = WorkState()
        self._budget = RuntimeBudget()

    def record_notification(self, kind: str, text: str, evidence_id: str) -> ObservedEvent:
        if kind not in {"user", "environment"}:
            raise ValueError("Only delivered USER/ENV notifications are observable")
        if not evidence_id:
            raise ValueError("Notification requires provenance evidence")
        if kind == "user":
            self._task_description = (self._task_description + "\n" if self._task_description else "") + text
        return self._append(kind, evidence_id, text=text)

    def record_action(self, tool_name: str, evidence_id: str) -> ObservedEvent:
        if not tool_name or not evidence_id:
            raise ValueError("Action requires a tool and evidence ID")
        return self._append("action", evidence_id, tool_name=tool_name)

    def record_tool_result(self, tool_name: str, evidence_id: str) -> ObservedEvent:
        if not tool_name or not evidence_id:
            raise ValueError("Tool result requires a tool and evidence ID")
        # Raw tool output stays in ProvenanceStore; the planning view has its ID.
        return self._append("tool_result", evidence_id, tool_name=tool_name)

    def record_work(self, work: WorkState) -> None:
        self._work = work

    def record_budget(self, budget: RuntimeBudget) -> None:
        self._budget = budget

    def snapshot(self, topology_version: int | None = None) -> OnlineObservation:
        events = tuple(self._events)
        return OnlineObservation(
            self._task_description, len(events), events, topology_version,
            self._work, self._budget,
            tuple(dict.fromkeys(event.evidence_id for event in events)),
        )

    def _append(self, kind: str, evidence_id: str, *, text: str | None = None,
                tool_name: str | None = None) -> ObservedEvent:
        event = ObservedEvent(len(self._events) + 1, kind, evidence_id, text, tool_name)
        self._events.append(event)
        return event
