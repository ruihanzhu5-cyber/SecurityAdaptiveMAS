"""Evidence-linked online task graph contract; extraction is replaceable."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from .online_observation import OnlineObservation


class Certainty(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class TaskStatus(str, Enum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"


@dataclass(frozen=True)
class Subtask:
    task_id: str
    description: str
    status: TaskStatus
    evidence_ids: tuple[str, ...]
    suggested_status: TaskStatus | None = None
    certainty: Certainty = Certainty.INFERRED

    @property
    def actual_status(self) -> TaskStatus:
        """Compatibility: the original status field remains the factual status."""
        return self.status


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
    requirement_id: str | None = None
    provider_task_id: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.task_id, self.requirement_id or self.description


@dataclass(frozen=True)
class TaskNeed:
    """A visible information need; completion is never inferred from prose."""
    task_id: str
    need_id: str
    required_kind: str
    required_fields: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    provider_task_id: str | None
    prerequisites: tuple[str, ...]
    owner_instance_id: str
    status: str
    status_evidence_ids: tuple[str, ...]


def task_need_snapshot(analysis: "TaskAnalysis", graph, objects) -> tuple[TaskNeed, ...]:
    """Derive statuses from current objects and executable graph relationships."""
    needs = []
    for requirement in analysis.information_requirements:
        provider = requirement.provider_task_id
        consumer = graph.assignments.get(requirement.task_id, "agent_0")
        available = [obj for obj in objects.values()
                     if provider is not None and obj["task_id"] == provider
                     and obj["status"] == "output_available"]
        readable = [obj for obj in available
                    if (consumer, obj["object_id"]) in graph.object_access]
        if readable:
            status = "available"  # Not satisfied: content has no field-level validator.
        elif available:
            status = "blocked"
        elif provider in graph.assignments:
            producer = graph.assignments[provider]
            status = "producible" if producer == consumer or (producer, consumer) in graph.extra_edges else "blocked"
        else:
            status = "unknown"
        needs.append(TaskNeed(requirement.task_id,
                              requirement.requirement_id or requirement.description,
                              "information", (), requirement.evidence_ids, provider,
                              tuple(edge.predecessor_id for edge in analysis.dependencies
                                    if edge.successor_id == requirement.task_id),
                              consumer, status,
                              tuple(obj["object_id"] for obj in readable)))
    return tuple(needs)


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
            if not isinstance(task.status, TaskStatus) or not isinstance(task.certainty, Certainty):
                raise ValueError("Invalid task state or certainty")
            if task.suggested_status is not None and not isinstance(task.suggested_status, TaskStatus):
                raise ValueError("Invalid suggested task status")
        if len({item.key for item in self.information_requirements}) != len(self.information_requirements):
            raise ValueError("Duplicate information requirement")
        for requirement in self.information_requirements:
            if not isinstance(requirement.certainty, Certainty):
                raise ValueError("Invalid information requirement certainty")
            if requirement.task_id not in ids or not requirement.evidence_ids or not set(requirement.evidence_ids) <= visible:
                raise ValueError("Information requirement lacks visible task/evidence")
            if requirement.provider_task_id is not None and requirement.provider_task_id not in ids:
                raise ValueError("Information provider refers to invalid task")
        if len({(e.predecessor_id, e.successor_id) for e in self.dependencies}) != len(self.dependencies):
            raise ValueError("Duplicate task dependency")
        for edge in self.dependencies:
            if edge.predecessor_id not in ids or edge.successor_id not in ids or edge.predecessor_id == edge.successor_id:
                raise ValueError("Dependency refers to invalid task")
            if not set(edge.evidence_ids) <= visible:
                raise ValueError("Dependency cites unseen evidence")
            if not isinstance(edge.certainty, Certainty):
                raise ValueError("Invalid dependency certainty")
            if edge.certainty != Certainty.UNKNOWN and not edge.evidence_ids:
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
    source_evidence_ids: tuple[str, ...] = ()
    as_of_sequence: int | None = None
    remove_dependencies: tuple[tuple[str, str], ...] = ()
    execution_contracts: tuple[dict, ...] = ()
    role_specs: tuple = ()
    need_specs: tuple = ()
    repair_specs: tuple[dict, ...] = ()
    remove_subtasks: tuple[str, ...] = ()
    remove_need_ids: tuple[str, ...] = ()


class TaskAnalyzerProtocol(Protocol):
    def analyze(self, observation: OnlineObservation,
                updates: tuple[TaskUpdate, ...] = ()) -> TaskAnalysis: ...


class OnlineTaskAnalyzer:
    """Incremental requirements; proposals never write execution facts."""

    def analyze(self, observation: OnlineObservation,
                updates: tuple[TaskUpdate, ...] = ()) -> TaskAnalysis:
        user_ids = tuple(event.evidence_id for event in observation.user_events)
        root = ((Subtask("root", observation.task_description, TaskStatus.PENDING, user_ids),)
                if observation.task_description else ())
        analysis = self._assemble(observation, root, (), ())
        for update in updates:
            analysis = self.apply_proposal(analysis, update, observation)
        return analysis

    def refresh(self, previous: TaskAnalysis, observation: OnlineObservation) -> TaskAnalysis:
        """Advance visibility and the one root without re-inferring existing work."""
        if previous.as_of_sequence > observation.sequence:
            raise ValueError("Cannot refresh task state from an older observation")
        tasks = {task.task_id: task for task in previous.subtasks}
        user_ids = tuple(event.evidence_id for event in observation.user_events)
        old = tasks.get("root")
        if observation.task_description:
            tasks["root"] = Subtask(
                "root", observation.task_description,
                old.status if old else TaskStatus.PENDING,
                user_ids,
                old.suggested_status if old else None,
                old.certainty if old else Certainty.INFERRED,
            )
        return self._assemble(observation, tuple(tasks.values()), previous.dependencies,
                              previous.information_requirements)

    def apply_proposal(self, previous: TaskAnalysis, proposal: TaskUpdate,
                       observation: OnlineObservation) -> TaskAnalysis:
        """Upsert by stable IDs; evidence visibility is not semantic confirmation."""
        if any(item.task_id == "root" for item in proposal.subtasks):
            raise ValueError("root_requires_delivered_user_notification")
        previous = self.refresh(previous, observation)
        if proposal.as_of_sequence is not None and proposal.as_of_sequence != observation.sequence:
            raise ValueError("Task proposal does not match the observation sequence")
        visible = set(observation.evidence_ids)
        if not set(proposal.source_evidence_ids) <= visible:
            raise ValueError("Task proposal cites unseen evidence")
        if len({task.task_id for task in proposal.subtasks}) != len(proposal.subtasks):
            raise ValueError("Duplicate task ID in proposal")
        tasks = {task.task_id: task for task in previous.subtasks}
        for task_id in proposal.remove_subtasks:
            if task_id == "root" or task_id not in tasks or not proposal.source_evidence_ids:
                raise ValueError("invalid_working_plan_retraction")
            del tasks[task_id]
        for item in proposal.subtasks:
            if not isinstance(item.certainty, Certainty):
                raise ValueError("Invalid proposal task certainty")
            if not item.evidence_ids or not set(item.evidence_ids) <= visible:
                raise ValueError("Task proposal cites unseen evidence or lacks evidence")
            old = tasks.get(item.task_id)
            if old is None and any(t.description == item.description and
                                   t.evidence_ids == item.evidence_ids for t in tasks.values()):
                raise ValueError("Repeated work must reuse its existing task ID")
            tasks[item.task_id] = replace(
                item, status=old.status if old else TaskStatus.PENDING,
                evidence_ids=tuple(dict.fromkeys((*(old.evidence_ids if old else ()), *item.evidence_ids))),
                suggested_status=item.suggested_status or item.status,
                certainty=Certainty.UNKNOWN if item.certainty == Certainty.UNKNOWN else Certainty.INFERRED,
            )
        dependencies = {(e.predecessor_id, e.successor_id): e for e in previous.dependencies}
        dependencies = {key: value for key, value in dependencies.items() if set(key) <= tasks.keys()}
        if proposal.remove_dependencies and not proposal.source_evidence_ids:
            raise ValueError("Dependency removal requires visible source evidence")
        for key in proposal.remove_dependencies:
            if key not in dependencies:
                raise ValueError("Cannot remove an unregistered dependency")
            del dependencies[key]
        for edge in proposal.dependencies:
            if not isinstance(edge.certainty, Certainty):
                raise ValueError("Invalid proposal dependency certainty")
            if not set(edge.evidence_ids) <= visible:
                raise ValueError("Task proposal cites unseen evidence")
            key = edge.predecessor_id, edge.successor_id
            old = dependencies.get(key)
            dependencies[key] = replace(
                edge, certainty=Certainty.UNKNOWN if edge.certainty == Certainty.UNKNOWN else Certainty.INFERRED,
                evidence_ids=tuple(dict.fromkeys((*(old.evidence_ids if old else ()), *edge.evidence_ids))),
            )
        requirements = {item.key: item for item in previous.information_requirements}
        requirements = {key: item for key, item in requirements.items()
                        if item.task_id in tasks and (item.provider_task_id is None or item.provider_task_id in tasks)}
        for item in proposal.information_requirements:
            if not isinstance(item.certainty, Certainty):
                raise ValueError("Invalid proposal information certainty")
            if not item.evidence_ids or not set(item.evidence_ids) <= visible:
                raise ValueError("Task proposal cites unseen evidence or lacks evidence")
            old = requirements.get(item.key)
            requirements[item.key] = replace(
                item, certainty=Certainty.UNKNOWN if item.certainty == Certainty.UNKNOWN else Certainty.INFERRED,
                evidence_ids=tuple(dict.fromkeys((*(old.evidence_ids if old else ()), *item.evidence_ids))),
            )
        return self._assemble(observation, tuple(tasks.values()), tuple(dependencies.values()),
                              tuple(requirements.values()))

    @staticmethod
    def _assemble(observation, tasks, dependencies, requirements) -> TaskAnalysis:
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
