import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from security_adaptive_mas.are_bridge import ObservedContext
from security_adaptive_mas.config import ROOT, load_config
from security_adaptive_mas.controller import Controller
from security_adaptive_mas.deepseek import (API_BASE, MODEL, PROVIDER, DeepSeekClient,
                                           DeepSeekEngineBuilder, RunBudget, parse_tool_calls)
from security_adaptive_mas.trace import TraceLogger


def fake_response(*, tool_arguments='{"key":"A"}'):
    call = SimpleNamespace(id="call_1", function=SimpleNamespace(
        name="read_file", arguments=tool_arguments))
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[call]))])


def test_deepseek_fixed_route_history_usage_and_tool_parse(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    trace = TraceLogger(ROOT / "work" / "test_logs" / f"{uuid4().hex}.jsonl")
    budget = RunBudget(trace, max_requests=1, max_total_tokens=5000, max_cost_usd=0.05)
    seen = []
    client = DeepSeekClient(budget, completion_fn=lambda **kwargs: (seen.append(kwargs), fake_response())[1])
    history = [{"role": "user", "content": "first"},
               {"role": "assistant", "content": "reply"},
               {"role": "user", "content": "next"}]
    result = client.complete(history, tools=[{"type": "function", "function": {
        "name": "read_file", "parameters": {"type": "object"}}}],
        response_format={"type": "json_object"})
    assert seen[0]["model"] == MODEL
    assert seen[0]["custom_llm_provider"] == PROVIDER
    assert seen[0]["api_base"] == API_BASE
    assert seen[0]["messages"] == history
    assert seen[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert seen[0]["temperature"] == 0
    assert "timeout" not in seen[0]
    assert parse_tool_calls(result) == [("call_1", "read_file", {"key": "A"})]
    assert budget.requests == 1 and budget.input_tokens == 12 and budget.output_tokens == 7
    with pytest.raises(RuntimeError, match="request budget"):
        client.complete(history)


def test_deepseek_rejects_missing_key_and_bad_tool_json(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    trace = TraceLogger(ROOT / "work" / "test_logs" / f"{uuid4().hex}.jsonl")
    client = DeepSeekClient(RunBudget(trace), completion_fn=lambda **_: fake_response())
    with pytest.raises(EnvironmentError, match="DEEPSEEK_API_KEY"):
        client.complete([{"role": "user", "content": "x"}])
    with pytest.raises(json.JSONDecodeError):
        parse_tool_calls(fake_response(tool_arguments="{"))


def test_runtime_input_provenance_is_observed_not_model_claimed():
    trace = TraceLogger(ROOT / "work" / "test_logs" / f"{uuid4().hex}.jsonl")
    controller = Controller(load_config(), trace, frozenset(), frozenset())
    task = controller.provenance.add("startup", "low_trust", "notification")
    context = ObservedContext(controller, task.evidence_id)
    context.record_model_input([{"role": "user", "content": "read A"}])
    first = context.current_evidence_id
    tool = controller.provenance.add("tool:Files", "low_trust", "content",
                                     owner_agent="agent_0")
    context.record_tool_output(tool.evidence_id)
    context.record_model_input([{"role": "user", "content": "tool result: content"}])
    second = context.current_evidence_id
    assert controller.provenance.derived_from(second, first)
    assert controller.provenance.derived_from(second, tool.evidence_id)
    assert controller.provenance.contains_low_trust(second)


def test_official_are_engine_uses_only_deepseek_route(monkeypatch):
    import sys
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    are_root = str(load_config().are_root)
    if are_root not in sys.path:
        sys.path.insert(0, are_root)
    from are.simulation.agents.are_simulation_agent_config import LLMEngineConfig

    trace = TraceLogger(ROOT / "work" / "test_logs" / f"{uuid4().hex}.jsonl")
    seen = []
    client = DeepSeekClient(RunBudget(trace, max_requests=1),
                            completion_fn=lambda **kwargs: (seen.append(kwargs), fake_response())[1])
    builder = DeepSeekEngineBuilder(client)
    with pytest.raises(ValueError, match="official DeepSeek"):
        builder.create_engine(LLMEngineConfig(model_name=MODEL, provider="openai", endpoint=API_BASE))
    engine = builder.create_engine(LLMEngineConfig(model_name=MODEL, provider=PROVIDER,
                                                   endpoint=API_BASE))
    text, metadata = engine.chat_completion([{"role": "user", "content": "hello"}])
    assert text == "ok" and metadata["usage"].prompt_tokens == 12
    assert seen[0]["api_base"] == API_BASE and seen[0]["custom_llm_provider"] == PROVIDER
