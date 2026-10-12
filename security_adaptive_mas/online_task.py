"""Evidence-linked online task graph contract; extraction is replaceable."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .online_observation import OnlineObservation


class Certainty(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"


@dataclass(frozen=True)
class Subtask:
    task_id: str
    description: str
    status: TaskStatus
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class TaskDependency:
    predecessor_id: str
    successor_id: str
    certainty: Certainty
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class InformationRequirement:
    task_id: str
    description: str
    certainty: Certainty
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class TaskAnalysis:
    as_of_sequence: int
    subtasks: tuple[Subtask, ...]
    dependencies: tuple[TaskDependency, ...]
    information_requirements: tuple[InformationRequirement, ...]
    evidence_ids: tuple[str, ...]
    unresolved_event_ids: tuple[str, ...] = ()
    parallelism: int | None = None
    critical_path_cost: float | None = None
    communication_demand: float | None = None
    shared_state_demand: float | None = None

    def validate(self, observation: OnlineObservation) -> None:
        if self.as_of_sequence != observation.sequence:
            raise ValueError("TaskAnalysis must match the observation sequence")
        visible = set(observation.evidence_ids)
        if not set(self.evidence_ids) <= visible:
            raise ValueError("TaskAnalysis cites unseen evidence")
        if not set(self.unresolved_event_ids) <= visible:
            raise ValueError("TaskAnalysis cites unseen unresolved event")
        ids = [task.task_id for task in self.subtasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate task ID")
        for task in self.subtasks:
            if not task.task_id or not task.description or not task.evidence_ids or not set(task.evidence_ids) <= visible:
                raise ValueError("Subtask lacks visible evidence")
        for requirement in self.information_requirements:
            if requirement.task_id not in ids or not requirement.evidence_ids or not set(requirement.evidence_ids) <= visible:
                raise ValueError("Information requirement lacks visible task/evidence")
        for edge in self.dependencies:
            if edge.predecessor_id not in ids or edge.successor_id not in ids or edge.predecessor_id == edge.successor_id:
                raise ValueError("Dependency refers to invalid task")
            if edge.certainty != Certainty.UNKNOWN and (not edge.evidence_ids or not set(edge.evidence_ids) <= visible):
                raise ValueError("Known dependency lacks visible evidence")
        graph = {task_id: [] for task_id in ids}
        for edge in self.dependencies:
            if edge.certainty != Certainty.UNKNOWN:
                graph[edge.predecessor_id].append(edge.successor_id)
        visiting, done = set(), set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("Task dependency cycle")
            if node in done:
                return
            visiting.add(node)
            for successor in graph[node]:
                visit(successor)
            visiting.remove(node)
            done.add(node)

        for task_id in ids:
            visit(task_id)


@dataclass(frozen=True)
class TaskUpdate:
    """Explicit, evidence-backed proposal from a replaceable extractor."""
    subtasks: tuple[Subtask, ...] = ()
    dependencies: tuple[TaskDependency, ...] = ()
    information_requirements: tuple[InformationRequirement, ...] = ()


class TaskAnalyzerProtocol(Protocol):
    def analyze(self, observation: OnlineObservation,
                updates: tuple[TaskUpdate, ...] = ()) -> TaskAnalysis: ...


class OnlineTaskAnalyzer:
    """Conservative baseline: no automatic task inference from ENV prose."""

    def analyze(self, observation: OnlineObservation,
                updates: tuple[TaskUpdate, ...] = ()) -> TaskAnalysis:
        user_ids = tuple(event.evidence_id for event in observation.user_events)
        root = ((Subtask("root", observation.task_description, TaskStatus.PENDING, user_ids),)
                if observation.task_description else ())
        tasks = root + tuple(task for update in updates for task in update.subtasks)
        dependencies = tuple(edge for update in updates for edge in update.dependencies)
        requirements = tuple(item for update in updates for item in update.information_requirements)
        referenced = tuple(e for task in tasks for e in task.evidence_ids) + tuple(
            e for edge in dependencies for e in edge.evidence_ids) + tuple(
            e for item in requirements for e in item.evidence_ids)
        covered = set(referenced)
        unresolved = tuple(event.evidence_id for event in observation.environment_events
                           if event.evidence_id not in covered)
        evidence = tuple(dict.fromkeys((*referenced, *unresolved)))
        analysis = TaskAnalysis(observation.sequence, tasks, dependencies, requirements,
                                evidence, unresolved)
        analysis.validate(observation)
        return analysis
