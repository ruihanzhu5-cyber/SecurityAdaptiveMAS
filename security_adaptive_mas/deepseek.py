"""Bounded DeepSeek V4.1 Flash calls through ARE's LiteLLM engine seam."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

MODEL = "deepseek-flash"
PROVIDER = "deepseek"
API_BASE = "https://api.deepseek.com"
# Official peak rates on 2026-10-12; these are conservative estimates, not bills.
PEAK_INPUT_USD_PER_TOKEN = 0.30 / 1_000_000
PEAK_OUTPUT_USD_PER_TOKEN = 1.20 / 1_000_000


@dataclass(frozen=True)
class DeepSeekSettings:
    max_output_tokens: int = 4096
    temperature: float = 0.0
    thinking: str = "disabled"
    timeout_seconds: float = 45.0
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.thinking != "disabled":
            raise ValueError("This adapter requires non-thinking mode for temperature=0")
        if self.max_output_tokens < 1 or self.max_attempts not in (1, 2):
            raise ValueError("Invalid DeepSeek request limits")


class BudgetExceeded(RuntimeError):
    """The existing episode budget refused a request before it was sent."""


class RunBudget:
    def __init__(self, trace, max_requests: int = 4, max_total_tokens: int = 100_000,
                 max_cost_usd: float = 0.05, mode: str = "real_are_single"):
        self.trace = trace
        self.mode = mode
        self.max_requests = max_requests
        self.max_total_tokens = max_total_tokens
        self.max_cost_usd = max_cost_usd
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.peak_cost_estimate_usd = 0.0
        self.latency_seconds = 0.0
        self.task_analysis_requests = 0
        self.task_analysis_request_limit: int | None = None
        self.requests_by_purpose: dict[str, int] = {}
        self.unaccounted_usage_notes: list[dict] = []
        self.unknown_cost_bound = 0.0
        self.unknown_token_bound = 0
        self.request_reservation = (0, 0.0)

    def before_request(self, messages: list[dict[str, Any]], max_output_tokens: int, *,
                       purpose: str | None = None, tools=None, response_format=None) -> None:
        # DeepSeek's tokenizer is unavailable locally. UTF-8 byte count of the
        # text request is a conservative upper bound for text tokenization.
        payload = {"messages": messages, "tools": tools, "response_format": response_format}
        input_bound = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        estimated_cost = (input_bound * PEAK_INPUT_USD_PER_TOKEN +
                          max_output_tokens * PEAK_OUTPUT_USD_PER_TOKEN)
        if (purpose == "task_analysis" and self.task_analysis_request_limit is not None and
                self.task_analysis_requests >= self.task_analysis_request_limit):
            raise BudgetExceeded("DeepSeek task-analysis request limit exhausted")
        if self.requests >= self.max_requests:
            raise BudgetExceeded("DeepSeek request budget exhausted")
        if self.input_tokens + self.output_tokens + self.unknown_token_bound + input_bound + max_output_tokens > self.max_total_tokens:
            raise BudgetExceeded("DeepSeek token budget exhausted")
        if self.peak_cost_estimate_usd + self.unknown_cost_bound + estimated_cost > self.max_cost_usd:
            raise BudgetExceeded("DeepSeek cost budget exhausted")
        self.requests += 1
        label = purpose or "execution"
        self.requests_by_purpose[label] = self.requests_by_purpose.get(label, 0) + 1
        self.request_reservation = (input_bound + max_output_tokens, estimated_cost)
        if purpose == "task_analysis":
            self.task_analysis_requests += 1

    def unknown_response(self, error, purpose):
        tokens, cost = self.request_reservation
        self.unknown_token_bound += tokens
        self.unknown_cost_bound += cost
        note = {"request": self.requests, "purpose": purpose,
                "exception_type": type(error).__name__, "reserved_cost_bound": cost,
                "billing_status": "unknown_not_actual_usage"}
        self.unaccounted_usage_notes.append(note)
        self.trace.log("model_usage_unknown", **note)
        self.request_reservation = (0, 0.0)

    def after_response(self, usage: Any, latency: float, *, purpose: str | None = None) -> None:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        if prompt == 0 and completion == 0:
            raise RuntimeError("DeepSeek response lacks token usage; cost cannot be accounted")
        self.input_tokens += prompt
        self.output_tokens += completion
        self.latency_seconds += latency
        self.peak_cost_estimate_usd += (
            prompt * PEAK_INPUT_USD_PER_TOKEN + completion * PEAK_OUTPUT_USD_PER_TOKEN
        )
        self.request_reservation = (0, 0.0)
        self.trace.log("model_usage", mode=self.mode, model=MODEL, provider=PROVIDER,
                       prompt_tokens=prompt, completion_tokens=completion,
                       latency_seconds=latency,
                       peak_cost_estimate_usd=self.peak_cost_estimate_usd,
                       request_count=self.requests,
                       **({"purpose": purpose} if purpose is not None else {}))


class DeepSeekClient:
    def __init__(self, budget: RunBudget, settings: DeepSeekSettings = DeepSeekSettings(),
                 completion_fn: Callable[..., Any] | None = None):
        self.budget, self.settings = budget, settings
        self.completion_fn = completion_fn

    def complete(self, messages: list[dict[str, Any]], *, tools=None,
                 response_format=None, on_input: Callable[[list[dict[str, Any]]], None] | None = None,
                 purpose: str | None = None):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise EnvironmentError("DEEPSEEK_API_KEY is not set")
        if self.completion_fn is None:
            from litellm import completion
            completion_fn = completion
        else:
            completion_fn = self.completion_fn
        kwargs = dict(model=MODEL, custom_llm_provider=PROVIDER, api_base=API_BASE,
                      api_key=api_key, messages=messages,
                      temperature=self.settings.temperature,
                      max_tokens=self.settings.max_output_tokens,
                      timeout=self.settings.timeout_seconds, num_retries=0,
                      extra_body={"thinking": {"type": self.settings.thinking}})
        if tools is not None:
            kwargs["tools"] = tools
        if response_format is not None:
            kwargs["response_format"] = response_format
        from litellm.exceptions import RateLimitError, Timeout
        if on_input is not None:
            on_input(messages)
        for attempt in range(self.settings.max_attempts):
            try:
                self.budget.before_request(messages, self.settings.max_output_tokens, purpose=purpose,
                                           tools=tools, response_format=response_format)
            except BudgetExceeded as error:
                if "task-analysis" not in str(error):
                    self.budget.trace.log("budget_exhausted", purpose=purpose, reason=str(error))
                raise
            started = time.monotonic()
            try:
                response = completion_fn(**kwargs)
                self.budget.after_response(response.usage, time.monotonic() - started, purpose=purpose)
            except (RateLimitError, Timeout) as error:
                self.budget.unknown_response(error, purpose)
                self.budget.trace.log("model_retryable_error", model=MODEL,
                                      error_type=type(error).__name__, attempt=attempt + 1,
                                      **({"purpose": purpose} if purpose is not None else {}))
                if attempt + 1 == self.settings.max_attempts:
                    raise
                time.sleep(1)
                continue
            except Exception as error:
                self.budget.unknown_response(error, purpose)
                raise
            return response
        raise RuntimeError("DeepSeek call did not return")


def parse_tool_calls(response: Any) -> list[tuple[str, str, dict[str, Any]]]:
    calls = getattr(response.choices[0].message, "tool_calls", None) or []
    parsed = []
    for call in calls:
        arguments = json.loads(call.function.arguments)
        if not isinstance(arguments, dict):
            raise ValueError("DeepSeek tool arguments must be a JSON object")
        parsed.append((call.id, call.function.name, arguments))
    return parsed


class DeepSeekEngineBuilder:
    """Supplies a bounded LiteLLMEngine to ARE's official AgentBuilder."""

    def __init__(self, client: DeepSeekClient, on_input=None, on_error=None):
        self.client, self.on_input = client, on_input
        self.on_error = on_error

    def create_engine(self, engine_config, mock_responses=None):
        if mock_responses is not None:
            raise ValueError("Use offline_mock mode for mock responses")
        if (engine_config.model_name != MODEL or engine_config.provider != PROVIDER or
                engine_config.endpoint != API_BASE):
            raise ValueError("ARE model must route only to the official DeepSeek endpoint")
        from are.simulation.agents.llm.litellm.litellm_engine import LiteLLMEngine, LiteLLMModelConfig

        client, on_input, on_error = self.client, self.on_input, self.on_error

        class BoundedLiteLLMEngine(LiteLLMEngine):
            def chat_completion(self, messages, stop_sequences=None, **kwargs):
                converted = [self._convert_message_to_litellm_format(item) for item in messages]
                try:
                    response = client.complete(converted, on_input=on_input, purpose="coordinator")
                except Exception as error:
                    if on_error is not None:
                        on_error(error)
                    raise
                content = response.choices[0].message.content
                if content is None:
                    raise RuntimeError("DeepSeek returned no text for ARE JSON-action loop")
                for token in stop_sequences or ():
                    content = content.split(token)[0]
                return content, {"usage": response.usage}

        return BoundedLiteLLMEngine(LiteLLMModelConfig(
            model_name=MODEL, provider=PROVIDER, endpoint=API_BASE,
            api_key=os.environ.get("DEEPSEEK_API_KEY")))
