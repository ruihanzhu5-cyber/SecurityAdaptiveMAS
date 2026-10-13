from __future__ import annotations

from dataclasses import asdict, dataclass

from .organization import OrganizationSelector, TaskAnalyzer, TopologyPlanner
from .online_observation import (Knowledge, ObservedEvent, OnlineObservation,
                                 OnlineObservationRecorder, RuntimeBudget)
from .online_task import InformationRequirement, OnlineTaskAnalyzer, TaskAnalysis, TaskUpdate
from .deepseek import BudgetExceeded
from .performance_monitor import PerformanceMonitor, PerformanceSignals, TriggerAction, TriggerDecision
from .task_proposer import TaskProposalError, TaskProposer
from .topology_candidates import (BaselinePerformanceEvaluator, TemplateCandidateGenerator,
                                  TopologyValidator)
from .security import MessageGateway, ProvenanceStore, TaskAuthorizationPolicy, ToolGateway


@dataclass(frozen=True)
class OrganizationSnapshot:
    pattern: str
    version: int
    agent_ids: tuple[str, ...]
    assignments: dict[str, str]


class Controller:
    def __init__(self, config, trace, authorized_tools: frozenset[str],
                 authorized_resources: frozenset[str], *, monitor_enabled: bool = False,
                 task_llm_enabled: bool = False, task_proposer: TaskProposer | None = None,
                 max_analysis_requests: int = 3,
                 runtime_agent_instances: tuple[str, ...] | None = None):
        if max_analysis_requests < 1:
            raise ValueError("Analysis request limit must be positive")
        self.trace = trace
        self.authorized_tools = authorized_tools
        self.analyzer = TaskAnalyzer()
        self.online_observations = OnlineObservationRecorder()
        self.run_budget = None
        self.online_analyzer = OnlineTaskAnalyzer()
        self.task_updates: list[TaskUpdate] = []
        self.task_analysis = None
        self.monitor_enabled = monitor_enabled
        self.task_llm_enabled = task_llm_enabled
        self.task_proposer = task_proposer
        self.max_analysis_requests = max_analysis_requests
        self.analysis_requests = 0
        self.runtime_agent_instances = runtime_agent_instances
        self.performance_monitor = PerformanceMonitor()
        self.pending_analysis = {}
        self.candidate_generator = TemplateCandidateGenerator(config.max_agents)
        self.candidate_validator = TopologyValidator(config.max_agents, authorized_tools)
        self.performance_evaluator = BaselinePerformanceEvaluator()
        self.planner = TopologyPlanner(config.manta_root, config.max_agents)
        self.selector = OrganizationSelector(self.planner, config.weights)
        self.provenance = ProvenanceStore(trace)
        self.messages = MessageGateway(self.provenance, trace)
        self.authorization = TaskAuthorizationPolicy(self.provenance, trace)
        self.tools = ToolGateway(self.provenance, trace, authorized_tools,
                                 authorized_resources, self.authorization)
        self.spec = None
        self.assignments: dict[str, str] = {}
        self.pattern = ""
        self.quarantined: set[str] = set()
        self.messages.blocked_agents = self.quarantined
        self.tools.blocked_agents = self.quarantined

    def quarantine(self, agent_id: str, mode: str = "full") -> None:
        if mode == "full":
            self.quarantined.add(agent_id)
        self.messages.quarantine(agent_id, mode)

    def update(self, task_text: str, pending_subtasks: tuple[str, ...], phase: str,
               observed_tool_calls: int = 0, risky_input: bool = False) -> OrganizationSnapshot:
        state = self.analyzer.analyze(task_text, pending_subtasks, phase,
                                      observed_tool_calls, risky_input)
        version = 1 if self.spec is None else self.spec.version + 1
        read_tools = frozenset(name for name, definition in self.tools.tools.items()
                               if definition.mode == "read" and name in self.authorized_tools)
        selected, trials = self.selector.choose(state, version, self.authorized_tools,
                                                read_tools)
        self.trace.log("organization_selection", phase=phase, candidates=trials,
                       selected=selected.pattern, reason=selected.reason)
        old_version = None if self.spec is None else self.spec.version
        # No gateway sees the MANTA intermediate state. Existing pending writes
        # stay reserved; duplicate fingerprints are never reset by reconfiguration.
        self.spec = selected.spec
        self.tools.current_spec = self.spec
        self.messages.current_spec = self.spec
        self.assignments = selected.assignments
        self.pattern = selected.pattern
        self.authorization.phase = phase
        if risky_input:
            for node in self.spec.agents:
                if node.structural_role == "worker":
                    self.quarantine(node.agent_id)
        self.trace.log("organization_changed", old_version=old_version,
                       new_version=self.spec.version, pattern=self.pattern,
                       assignments=self.assignments,
                       quarantined=sorted(self.quarantined),
                       revoked_old_authorizations=old_version is not None)
        return self.snapshot()

    def snapshot(self) -> OrganizationSnapshot:
        if self.spec is None:
            raise RuntimeError("Controller not initialized")
        return OrganizationSnapshot(self.pattern, self.spec.version,
                                    tuple(n.agent_id for n in self.spec.agents),
                                    dict(self.assignments))

    def online_snapshot(self) -> OnlineObservation:
        """Planning view from delivered runtime facts; never from Scenario."""
        if self.run_budget is not None:
            budget = self.run_budget
            self.online_observations.record_budget(RuntimeBudget(
                budget.max_requests - budget.requests,
                budget.max_total_tokens - budget.input_tokens - budget.output_tokens,
                budget.max_cost_usd - budget.peak_cost_estimate_usd,
                Knowledge.OBSERVED,
            ))
        return self.online_observations.snapshot(None if self.spec is None else self.spec.version)

    def online_candidates(self):
        """Structural proposals only; this does not switch the ARE runtime."""
        observation = self.online_snapshot()
        analysis = self._current_task_analysis(observation)
        candidates = self.candidate_generator.generate(analysis)
        for candidate in candidates:
            self.candidate_validator.validate(candidate, analysis)
        estimates = tuple(self.performance_evaluator.evaluate(candidate, analysis)
                          for candidate in candidates)
        return analysis, candidates, estimates

    def add_task_update(self, update: TaskUpdate) -> None:
        """Accept a proposed task change only if its evidence is online-visible."""
        snapshot = self.online_snapshot()
        previous = self._current_task_analysis(snapshot)
        self.task_analysis = self.online_analyzer.apply_proposal(previous, update, snapshot)
        if update not in self.task_updates:
            self.task_updates.append(update)

    def _current_task_analysis(self, observation: OnlineObservation) -> TaskAnalysis:
        self.task_analysis = (self.online_analyzer.analyze(observation) if self.task_analysis is None
                              else self.online_analyzer.refresh(self.task_analysis, observation))
        return self.task_analysis

    def validate_related_requirements(self, keys: tuple[tuple[str, str], ...]
                                      ) -> tuple[InformationRequirement, ...]:
        """For trusted scheduling callers; no relevance or completion inference."""
        observation = self.online_snapshot()
        return self.performance_monitor.validate_related_requirements(
            keys, self._current_task_analysis(observation))

    def on_observed_event(self, event: ObservedEvent, *,
                          related_requirements: tuple[tuple[str, str], ...] = ()) -> TriggerDecision:
        """Cheap Shadow prefilter only. Never invokes a model or changes a spec."""
        if not self.monitor_enabled:
            return TriggerDecision(TriggerAction.KEEP, event.sequence, ("monitor_disabled",), ())
        observation = self.online_snapshot()
        decision = self.performance_monitor.prefilter(
            event, observation, self._current_task_analysis(observation),
            related_requirements=related_requirements)
        if decision.action == TriggerAction.ANALYZE:
            self.pending_analysis[event.event_id] = event
        self.trace.log("prefilter_decision", **asdict(decision), event_id=event.event_id,
                       pending_analysis=event.event_id in self.pending_analysis,
                       related_requirements=related_requirements)
        return decision

    def process_pending_analysis(self, *, tool_evidence_ids: tuple[str, ...] = ()) -> TriggerDecision:
        """Explicit caller-chosen boundary; no automatic ARE scheduling in P1."""
        observation = self.online_snapshot()
        events = tuple(self.pending_analysis.values())

        def deferred(reason, action=TriggerAction.ANALYZE, *, analysis_succeeded=None):
            decision = TriggerDecision(action, observation.sequence, (reason,),
                                       tuple(event.evidence_id for event in events))
            if self.monitor_enabled:
                self.trace.log("trigger_decision", **asdict(decision),
                               event_ids=[event.event_id for event in events],
                               pending_analysis=bool(self.pending_analysis),
                               analysis_succeeded=analysis_succeeded, requirements_changed=None)
            return decision

        if not self.monitor_enabled:
            return deferred("monitor_disabled", TriggerAction.KEEP)
        if not events:
            return deferred("no_pending_analysis", TriggerAction.KEEP)
        if not self.task_llm_enabled:
            return deferred("analysis_deferred_disabled")
        if self.analysis_requests >= self.max_analysis_requests:
            return deferred("analysis_deferred_budget")
        if self.task_proposer is None:
            return deferred("task_proposer_unavailable")
        if self.run_budget is None or self.task_proposer.budget is not self.run_budget:
            return deferred("shared_episode_budget_unavailable")
        limit = self.run_budget.task_analysis_request_limit
        self.run_budget.task_analysis_request_limit = (
            self.max_analysis_requests if limit is None else min(limit, self.max_analysis_requests))
        self.analysis_requests = self.run_budget.task_analysis_requests
        if self.analysis_requests >= self.run_budget.task_analysis_request_limit:
            return deferred("analysis_deferred_budget")
        if self.run_budget.requests >= self.run_budget.max_requests:
            return deferred("analysis_deferred_budget")
        previous = self._current_task_analysis(observation)
        try:
            proposal = self.task_proposer.propose(observation, previous,
                                                  tool_evidence_ids=tool_evidence_ids)
            if not {event.evidence_id for event in events} <= set(proposal.source_evidence_ids):
                raise ValueError("Proposal does not account for every pending event")
            current = self.online_analyzer.apply_proposal(previous, proposal, observation)
        except BudgetExceeded:
            return deferred("analysis_deferred_budget")
        except TaskProposalError:
            return deferred("task_proposal_failed", analysis_succeeded=False)
        except ValueError as error:
            self.trace.log("task_proposal_error", as_of_sequence=observation.sequence,
                           error_type=type(error).__name__, reason=str(error))
            return deferred("task_proposal_failed", analysis_succeeded=False)
        finally:
            self.analysis_requests = self.run_budget.task_analysis_requests
        self.task_analysis = current
        if proposal not in self.task_updates:
            self.task_updates.append(proposal)
        self.performance_monitor.mark_analyzed(events, observation.sequence)
        for event in events:
            del self.pending_analysis[event.event_id]
        signals = self.performance_monitor.signals(
            observation, current, runtime_agent_instances=self.runtime_agent_instances)
        decision = self.performance_monitor.reconsider(previous, current, signals)
        self.trace.log("task_proposal_validated", as_of_sequence=observation.sequence,
                       task_ids=[task.task_id for task in proposal.subtasks],
                       source_evidence_ids=proposal.source_evidence_ids,
                       event_ids=[event.event_id for event in events],
                       analysis_succeeded=True, proposal_status="proposed_unverified",
                       requirements_changed=decision.action == TriggerAction.RECONSIDER)
        self.trace.log("performance_signals", as_of_sequence=observation.sequence, **asdict(signals))
        self.trace.log("trigger_decision", **asdict(decision),
                       event_ids=[event.event_id for event in events], pending_analysis=False,
                       analysis_succeeded=True,
                       requirements_changed=decision.action == TriggerAction.RECONSIDER)
        return decision

    def performance_snapshot(self) -> PerformanceSignals:
        observation = self.online_snapshot()
        return self.performance_monitor.signals(
            observation, self._current_task_analysis(observation),
            runtime_agent_instances=self.runtime_agent_instances)

    def safe_reconfigure(self, task_text: str, pending_subtasks: tuple[str, ...],
                         observed_tool_calls: int = 0) -> OrganizationSnapshot:
        self.trace.log("security_reconfiguration_requested", reason="low_trust_to_sensitive_write")
        return self.update(task_text, pending_subtasks, "commit",
                           observed_tool_calls, risky_input=True)
