import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from security_adaptive_mas.are_bridge import GuardedARETool, ObservedContext
from security_adaptive_mas.config import ROOT, load_config
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.deepseek import BudgetExceeded, DeepSeekClient, DeepSeekSettings, RunBudget
from security_adaptive_mas.graph_evaluation import coverage, project_risk
from security_adaptive_mas.graph_runtime import GraphRuntime, TopologyPatch
from security_adaptive_mas.online_task import InformationRequirement, Certainty, TaskAnalysis
from security_adaptive_mas.security import Message, TaskScopePolicy, ToolDefinition, UnsupportedTaskScope
from security_adaptive_mas.trace import TraceLogger


def setup_runtime():
    trace = TraceLogger(ROOT / 'work/test_logs' / (uuid4().hex + '.jsonl'))
    controller = Controller(load_config(), trace, frozenset({'read', 'write'}), frozenset({'MockApp'}), monitor_enabled=True)
    runtime = GraphRuntime(controller, {'read', 'write'}, {'read'})
    task = controller.provenance.add('delivered:user', 'trusted_task', 'Read independent materials')
    event = controller.online_observations.record_notification('user', task.text, task.evidence_id)
    controller.on_observed_event(event)
    runtime.add_object(task.evidence_id, 'agent_0')
    return controller, runtime, task


def patch(controller, runtime, operations, name='test'):
    return TopologyPatch(name, runtime.graph.version, controller.online_snapshot().sequence,
                         controller.task_revision, tuple(operations))


def test_instance_identity_effective_routes_and_object_access():
    controller, runtime, task = setup_runtime()
    runtime.apply(patch(controller, runtime, [
        {'op': 'SPAWN_WORKER', 'agent_id': 'worker_1', 'tools': ['read']},
        {'op': 'ADD_ROUTE', 'source': 'agent_0', 'target': 'worker_1'},
        {'op': 'SET_OBJECT_ACCESS', 'agent_id': 'worker_1', 'object_id': task.evidence_id, 'allowed': True}]))
    assert runtime.read_object('worker_1', task.evidence_id) == task.text
    message = Message('agent_0', 'worker_1', runtime.graph.version, task.evidence_id, task.text)
    controller.messages.send(runtime.graph, message)
    stale = patch(controller, runtime, [{'op': 'KEEP'}])
    runtime.apply(patch(controller, runtime, [
        {'op': 'REMOVE_ROUTE', 'source': 'agent_0', 'target': 'worker_1'},
        {'op': 'SET_OBJECT_ACCESS', 'agent_id': 'worker_1', 'object_id': task.evidence_id, 'allowed': False}]))
    assert controller.messages.deliver(runtime.graph) == []
    with pytest.raises(PermissionError, match='object_access_denied'):
        runtime.read_object('worker_1', task.evidence_id)
    with pytest.raises(ValueError, match='stale_topology_patch'):
        runtime.apply(stale)
    original = SimpleNamespace(name='write', description='', inputs={}, output_type='string',
        app_tool=SimpleNamespace(app_name='MockApp', write_operation=True))
    guarded = GuardedARETool(original, controller, ObservedContext(controller, task.evidence_id, 'worker_1'))
    with pytest.raises(PermissionError, match='tool_permission'):
        guarded()
    rows = [json.loads(line) for line in controller.trace.path.read_text(encoding='utf-8').splitlines()]
    assert rows[-1]['agent_id'] == 'worker_1'


def test_and_requirements_and_shared_object_bypass_survive_retirement():
    controller, runtime, task = setup_runtime()
    runtime.apply(patch(controller, runtime, [{'op': 'SPAWN_WORKER', 'agent_id': 'a', 'tools': ['read']},
        {'op': 'ADD_ROUTE', 'source': 'a', 'target': 'agent_0'}]))
    first = controller.provenance.add('tool', 'low_trust', 'x', owner_agent='a')
    second = controller.provenance.add('tool', 'low_trust', 'y', owner_agent='a')
    runtime.add_object(first.evidence_id, 'a', task_id='p1')
    runtime.add_object(second.evidence_id, 'a', task_id='p2')
    runtime.apply(patch(controller, runtime, [{'op': 'SET_OBJECT_ACCESS', 'agent_id': 'agent_0', 'object_id': first.evidence_id, 'allowed': True},
        {'op': 'REMOVE_ROUTE', 'source': 'a', 'target': 'agent_0'}]))
    needs = tuple(InformationRequirement('root', key, Certainty.INFERRED, (task.evidence_id,), key, provider) for key, provider in [('x', 'p1'), ('y', 'p2')])
    analysis = TaskAnalysis(0, (), (), needs, ())
    assert len(coverage(analysis, runtime.graph, runtime.objects)['known_missing']) == 1
    risk = project_risk(runtime.graph, runtime.objects, {'a'}, {'a'}, {'tool:write'})
    assert risk['witness_paths']['tool:write'] == ['a', first.evidence_id, 'agent_0', 'tool:write']
    runtime.apply(patch(controller, runtime, [{'op': 'RETIRE_WORKER', 'agent_id': 'a'}]))
    assert first.evidence_id in runtime.objects
    assert 'tool:write' in project_risk(runtime.graph, runtime.objects, {'a'}, {'a'}, {'tool:write'})['reachable_targets']
    assert project_risk(runtime.graph, runtime.objects, {'a'}, set(), ())['reachable_targets'] is None


def test_explicit_scope_allows_external_data_but_not_changed_controls():
    controller, runtime, task = setup_runtime()
    policy = TaskScopePolicy(controller.provenance, controller.trace)
    controller.tools.authorization = policy
    scoped = controller.provenance.add('delivered:user', 'trusted_task', json.dumps({'authorized_scopes': [{
        'tool': 'write', 'resource': 'MockApp', 'control_fields': {'path': '/authorized.txt'}, 'data_fields': ['content']}]}))
    policy.register_user(scoped.evidence_id)
    external = controller.provenance.add('tool', 'low_trust', 'untrusted report data', owner_agent='agent_0')
    writes = []
    controller.tools.register(ToolDefinition('write', 'write', frozenset({'MockApp'}), lambda **args: writes.append(args)))
    controller.tools.call(runtime.graph, 'agent_0', runtime.graph.version, 'write', 'MockApp',
                          {'path': '/authorized.txt', 'content': external.text}, external.evidence_id)
    with pytest.raises(UnsupportedTaskScope):
        controller.tools.call(runtime.graph, 'agent_0', runtime.graph.version, 'write', 'MockApp',
                              {'path': '/outside.txt', 'content': external.text}, external.evidence_id)
    with pytest.raises(ValueError, match='scope_requires_delivered_user'):
        policy.register_user(external.evidence_id)
    assert len(writes) == 1


def test_real_are_sandbox_write_with_explicit_user_path():
    from security_adaptive_mas.are_adapter import import_official_scenario
    config = load_config()
    scenario = import_official_scenario(config.select(config.scenario_ids[0]), config.are_root)
    from are.simulation.tool_utils import AppToolAdapter
    tool = next(tool for tool in scenario.get_tools() if tool.app_name == 'Files' and tool.func_name == 'mkdir')
    trace = TraceLogger(ROOT / 'work/test_logs' / (uuid4().hex + '.jsonl'))
    controller = Controller(config, trace, frozenset({tool._public_name}), frozenset({'Files'}))
    runtime = GraphRuntime(controller, {tool._public_name}, ())
    controller.tools.authorization = TaskScopePolicy(controller.provenance, trace)
    path = '/v01_authorized_' + uuid4().hex
    task = controller.provenance.add('delivered:user', 'trusted_task', 'create directory ' + path)
    controller.tools.authorization.register_user(task.evidence_id)
    context = ObservedContext(controller, task.evidence_id)
    guarded = GuardedARETool(AppToolAdapter(tool), controller, context)
    guarded(path=path)
    files = tool.class_instance
    assert files.exists(path)
    with pytest.raises(UnsupportedTaskScope):
        guarded(path=path + '_outside')


def test_schema_budget_and_worker_controller_accounting(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    controller, runtime, task = setup_runtime()
    budget = RunBudget(controller.trace, max_requests=3, max_total_tokens=50000, max_cost_usd=1)
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
                               choices=[SimpleNamespace(message=SimpleNamespace(content='ok'))])
    seen = []
    client = DeepSeekClient(budget, DeepSeekSettings(max_output_tokens=100), completion_fn=lambda **kw: (seen.append(kw), response)[1])
    client.complete([{'role': 'user', 'content': 'x'}], purpose='task_analysis')
    client.complete([{'role': 'user', 'content': 'x'}], purpose='worker:a')
    client.complete([{'role': 'user', 'content': 'x'}], purpose='coordinator')
    assert budget.requests == 3 and budget.input_tokens == 30
    assert budget.requests_by_purpose == {'task_analysis': 1, 'worker:a': 1, 'coordinator': 1}
    with pytest.raises(BudgetExceeded):
        client.complete([{'role': 'user', 'content': 'x'}])
    narrow = RunBudget(controller.trace, max_total_tokens=1000, max_cost_usd=1)
    with pytest.raises(BudgetExceeded):
        narrow.before_request([], 1, tools=[{'schema': 'z' * 2000}])


def test_online_proposal_drives_worker_calls_and_retirement(monkeypatch):
    from security_adaptive_mas.dynamic_team import FrameworkTeam
    from security_adaptive_mas.task_proposer import DeepSeekTaskProposer
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    controller, runtime, task = setup_runtime()
    controller.task_llm_enabled = True
    budget = RunBudget(controller.trace, max_requests=10, max_total_tokens=100000, max_cost_usd=1)
    controller.run_budget = budget
    proposal = {'source_evidence_ids': [task.evidence_id], 'subtasks': [
        {'task_id': key, 'description': 'Read ' + key, 'suggested_status': 'pending',
         'evidence_ids': [task.evidence_id], 'certainty': 'inferred'} for key in ('a', 'b')],
        'dependencies': [], 'information_requirements': [], 'execution_contracts': [
            {'task_id': key, 'read_only': True, 'required_tools': ['read'],
             'input_evidence_ids': [task.evidence_id], 'independent_of': [other]}
            for key, other in [('a', 'b'), ('b', 'a')]]}
    calls = []

    def completion(**kwargs):
        if kwargs.get('response_format'):
            message = SimpleNamespace(content=json.dumps(proposal), tool_calls=[])
        elif not any(row['role'] == 'tool' for row in kwargs['messages']):
            message = SimpleNamespace(content='', tool_calls=[SimpleNamespace(id=key, function=SimpleNamespace(name='read', arguments='{}')) for key in ('call1','call2')])
        else:
            message = SimpleNamespace(content='Worker finding', tool_calls=[])
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
                               choices=[SimpleNamespace(message=message)])

    class ReadTool:
        name, description, inputs, output_type = 'read', 'read', {}, 'string'
        app_tool = SimpleNamespace(app_name='MockApp', write_operation=False)
        def __call__(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise ValueError('ordinary invalid read parameter')
            return 'observed data'

    client = DeepSeekClient(budget, completion_fn=completion)
    context = ObservedContext(controller, task.evidence_id)
    controller.task_proposer = DeepSeekTaskProposer(client, lambda key: controller.provenance.items[key].text, execution_tools=('read',))
    team = FrameworkTeam(controller, runtime, client, context, {'read': ReadTool()}, 'adaptive')
    controller.framework_team = team
    controller.suspect_instances = set()
    messages = [{'role': 'user', 'content': task.text}]
    team.before_input(messages)
    assert len(calls) == 4 and team.worker_steps == 4
    assert budget.requests_by_purpose == {'task_analysis': 1, 'worker:worker_1_1': 2, 'worker:worker_1_2': 2}
    assert len(runtime.graph.agents) == 1 and runtime.max_active == 3
    assert runtime.applied == 3 and len(runtime.instances) == 3
    assert 'Worker outputs' in messages[-1]['content']
    parent_ids = controller.provenance.items[context.current_evidence_id].parent_ids
    assert all(key in parent_ids for key, obj in runtime.objects.items() if obj['task_id'] in {'a', 'b'})
    assert all(task.status.value == 'pending' for task in controller.task_analysis.subtasks)
