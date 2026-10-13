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
    event_id: str | None = None
    agent_instance_id: str | None = None
    call_id: str | None = None
    status: str | None = None
    simulation_time: float | None = None
    elapsed_seconds: float | None = None
    input_fingerprint: str | None = None
    input_characters: int | None = None


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

    def record_notification(self, kind: str, text: str, evidence_id: str,
                            **metadata) -> ObservedEvent:
        if kind not in {"user", "environment"}:
            raise ValueError("Only delivered USER/ENV notifications are observable")
        if not evidence_id:
            raise ValueError("Notification requires provenance evidence")
        before = len(self._events)
        event = self._append(kind, evidence_id, text=text, **metadata)
        if kind == "user" and len(self._events) > before:
            self._task_description = (self._task_description + "\n" if self._task_description else "") + text
        return event

    def record_action(self, tool_name: str, evidence_id: str, **metadata) -> ObservedEvent:
        if not tool_name or not evidence_id:
            raise ValueError("Action requires a tool and evidence ID")
        return self._append("action", evidence_id, tool_name=tool_name, **metadata)

    def record_tool_result(self, tool_name: str, evidence_id: str, **metadata) -> ObservedEvent:
        if not tool_name or not evidence_id:
            raise ValueError("Tool result requires a tool and evidence ID")
        # Raw tool output stays in ProvenanceStore; the planning view has its ID.
        return self._append("tool_result", evidence_id, tool_name=tool_name, **metadata)

    def record_model_input(self, evidence_id: str, fingerprint: str, characters: int,
                           *, agent_instance_id: str) -> ObservedEvent:
        """Exact whole-request measurement, not per-evidence token attribution."""
        if not evidence_id or not fingerprint or characters < 0:
            raise ValueError("Model input requires evidence and a measured payload")
        return self._append("model_input", evidence_id, status="observed",
                            agent_instance_id=agent_instance_id,
                            input_fingerprint=fingerprint, input_characters=characters)

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
                tool_name: str | None = None, **metadata) -> ObservedEvent:
        event_id = metadata.pop("event_id", None)
        if event_id is not None:
            for previous in self._events:
                if previous.event_id == event_id:
                    repeated = ObservedEvent(previous.sequence, kind, evidence_id, text,
                                             tool_name, event_id=event_id, **metadata)
                    if repeated != previous:
                        raise ValueError("Event ID reused for different facts")
                    return previous
        sequence = len(self._events) + 1
        if event_id is None and any(old.event_id == f"event_{sequence}" for old in self._events):
            raise ValueError("Generated event ID conflicts with a caller-supplied ID")
        event = ObservedEvent(sequence, kind, evidence_id, text, tool_name,
                              event_id=event_id or f"event_{sequence}", **metadata)
        self._events.append(event)
        return event
