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

    def before_request(self, messages: list[dict[str, Any]], max_output_tokens: int) -> None:
        # DeepSeek's tokenizer is unavailable locally. UTF-8 byte count of the
        # text request is a conservative upper bound for text tokenization.
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
        estimated_cost = (input_bound * PEAK_INPUT_USD_PER_TOKEN +
                          max_output_tokens * PEAK_OUTPUT_USD_PER_TOKEN)
        if self.requests >= self.max_requests:
            raise RuntimeError("DeepSeek request budget exhausted")
        if self.input_tokens + self.output_tokens + input_bound + max_output_tokens > self.max_total_tokens:
            raise RuntimeError("DeepSeek token budget exhausted")
        if self.peak_cost_estimate_usd + estimated_cost > self.max_cost_usd:
            raise RuntimeError("DeepSeek cost budget exhausted")
        self.requests += 1

    def after_response(self, usage: Any, latency: float) -> None:
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
        self.trace.log("model_usage", mode=self.mode, model=MODEL, provider=PROVIDER,
                       prompt_tokens=prompt, completion_tokens=completion,
                       latency_seconds=latency,
                       peak_cost_estimate_usd=self.peak_cost_estimate_usd,
                       request_count=self.requests)


class DeepSeekClient:
    def __init__(self, budget: RunBudget, settings: DeepSeekSettings = DeepSeekSettings(),
                 completion_fn: Callable[..., Any] | None = None):
        self.budget, self.settings = budget, settings
        self.completion_fn = completion_fn

    def complete(self, messages: list[dict[str, Any]], *, tools=None,
                 response_format=None, on_input: Callable[[list[dict[str, Any]]], None] | None = None):
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
        for attempt in range(self.settings.max_attempts):
            self.budget.before_request(messages, self.settings.max_output_tokens)
            if on_input is not None:
                on_input(messages)
            started = time.monotonic()
            try:
                response = completion_fn(**kwargs)
            except (RateLimitError, Timeout) as error:
                self.budget.trace.log("model_retryable_error", model=MODEL,
                                      error_type=type(error).__name__, attempt=attempt + 1)
                if attempt + 1 == self.settings.max_attempts:
                    raise
                time.sleep(1)
                continue
            self.budget.after_response(response.usage, time.monotonic() - started)
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

    def __init__(self, client: DeepSeekClient, on_input=None):
        self.client, self.on_input = client, on_input

    def create_engine(self, engine_config, mock_responses=None):
        if mock_responses is not None:
            raise ValueError("Use offline_mock mode for mock responses")
        if (engine_config.model_name != MODEL or engine_config.provider != PROVIDER or
                engine_config.endpoint != API_BASE):
            raise ValueError("ARE model must route only to the official DeepSeek endpoint")
        from are.simulation.agents.llm.litellm.litellm_engine import LiteLLMEngine, LiteLLMModelConfig

        client, on_input = self.client, self.on_input

        class BoundedLiteLLMEngine(LiteLLMEngine):
            def chat_completion(self, messages, stop_sequences=None, **kwargs):
                converted = [self._convert_message_to_litellm_format(item) for item in messages]
                response = client.complete(converted, on_input=on_input)
                content = response.choices[0].message.content
                if content is None:
                    raise RuntimeError("DeepSeek returned no text for ARE JSON-action loop")
                for token in stop_sequences or ():
                    content = content.split(token)[0]
                return content, {"usage": response.usage}

        return BoundedLiteLLMEngine(LiteLLMModelConfig(
            model_name=MODEL, provider=PROVIDER, endpoint=API_BASE,
            api_key=os.environ.get("DEEPSEEK_API_KEY")))
