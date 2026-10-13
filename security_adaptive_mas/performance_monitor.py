"""Python-only Shadow diagnostics. No model calls, task writes or topology apply."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .online_observation import ObservedEvent, OnlineObservation
from .online_task import InformationRequirement, TaskAnalysis


class TriggerAction(str, Enum):
    KEEP = "KEEP"
    ANALYZE = "ANALYZE"
    RECONSIDER = "RECONSIDER"


@dataclass(frozen=True)
class TriggerDecision:
    action: TriggerAction
    as_of_sequence: int
    reasons: tuple[str, ...]
    related_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class MetricValue:
    value: float | None
    status: str
    unit: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PerformanceSignals:
    parallel_opportunity: MetricValue
    information_needs: tuple[InformationRequirement, ...]
    context_redundancy: MetricValue
    coordination_waste: MetricValue
    ready_work_count: MetricValue = MetricValue(None, "unknown", "work_items")


class PerformanceMonitor:
    def __init__(self):
        self.last_analyzed_sequence = 0
        self.analyzed_event_ids: set[str] = set()

    def prefilter(self, event: ObservedEvent, snapshot: OnlineObservation,
                  analysis: TaskAnalysis, *,
                  related_requirements: tuple[tuple[str, str], ...] = ()) -> TriggerDecision:
        if event not in snapshot.events:
            raise ValueError("Prefilter event is not in the visible snapshot")
        if event.event_id is None:
            raise ValueError("Prefilter requires a stable event ID")
        reason = "ordinary_runtime_event"
        action = TriggerAction.KEEP
        if event.event_id in self.analyzed_event_ids:
            reason = "event_already_analyzed"
        elif event.kind in {"user", "environment"}:
            action = TriggerAction.ANALYZE
            reason = "delivered_user_task" if event.kind == "user" else "delivered_environment_notification"
        elif related_requirements:
            registered = {item.key for item in analysis.information_requirements}
            if (event.kind != "tool_result" or event.status != "completed" or
                    not set(related_requirements) <= registered):
                raise ValueError("Information-result link requires a completed result and registered need")
            # Caller supplies the link; this does not confirm that the need is fulfilled.
            action, reason = TriggerAction.ANALYZE, "registered_information_result"
        return TriggerDecision(action, snapshot.sequence, (reason,), (event.evidence_id,))

    def mark_analyzed(self, events: tuple[ObservedEvent, ...], sequence: int) -> None:
        self.analyzed_event_ids.update(event.event_id for event in events if event.event_id is not None)
        self.last_analyzed_sequence = max(self.last_analyzed_sequence, sequence)

    def signals(self, snapshot: OnlineObservation, analysis: TaskAnalysis, *,
                runtime_agent_instances: tuple[str, ...] | None = None) -> PerformanceSignals:
        inputs = [event for event in snapshot.events if event.kind == "model_input"]
        fingerprints: set[str] = set()
        duplicate_characters = 0
        duplicate_ids = []
        for event in inputs:
            if event.input_fingerprint is None or event.input_characters is None:
                raise ValueError("Model input measurement is incomplete")
            if event.input_fingerprint in fingerprints:
                duplicate_characters += event.input_characters
                duplicate_ids.append(event.evidence_id)
            fingerprints.add(event.input_fingerprint)
        context = (MetricValue(float(duplicate_characters), "observed", "repeated_request_characters",
                               tuple(duplicate_ids)) if inputs else
                   MetricValue(None, "unknown", "repeated_request_characters"))
        coordination = MetricValue(
            None, "not_applicable" if runtime_agent_instances is not None and
            len(runtime_agent_instances) == 1 else "unknown", "agent_coordination_seconds")
        return PerformanceSignals(
            MetricValue(None, "unknown", "fraction_of_total_work"),
            analysis.information_requirements, context, coordination,
        )

    def reconsider(self, previous: TaskAnalysis, current: TaskAnalysis,
                   signals: PerformanceSignals) -> TriggerDecision:
        def structure(analysis):
            return (
                tuple((t.task_id, t.description) for t in analysis.subtasks),
                tuple((e.predecessor_id, e.successor_id, e.certainty) for e in analysis.dependencies),
                tuple((r.key, r.description, r.provider_task_id, r.certainty)
                      for r in analysis.information_requirements),
            )
        changed = structure(previous) != structure(current)
        return TriggerDecision(
            TriggerAction.RECONSIDER if changed else TriggerAction.KEEP,
            current.as_of_sequence,
            ("task_requirements_changed",) if changed else ("requirements_unchanged", "insufficient_evidence"),
            current.evidence_ids,
        )
