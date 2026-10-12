"""One paid DeepSeek chat request, only after explicit cost acknowledgement."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from .config import ROOT
from .deepseek import API_BASE, MODEL, DeepSeekClient, RunBudget
from .trace import TraceLogger


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-model-cost", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_model_cost:
        parser.error("A real model call requires --acknowledge-model-cost")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("DEEPSEEK_API_KEY is not set")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trace = TraceLogger(ROOT / "results" / "deepseek" / f"probe_{stamp}.jsonl",
                        mode="real_connectivity_probe")
    budget = RunBudget(trace, max_requests=1, max_total_tokens=5000,
                       max_cost_usd=0.01, mode="real_connectivity_probe")
    response = DeepSeekClient(budget).complete([
        {"role": "user", "content": "Reply with only the word OK."}
    ])
    print(json.dumps({"model": MODEL, "api_base": API_BASE,
                      "reply": response.choices[0].message.content,
                      "requests": budget.requests,
                      "input_tokens": budget.input_tokens,
                      "output_tokens": budget.output_tokens,
                      "latency_seconds": budget.latency_seconds,
                      "peak_cost_estimate_usd": budget.peak_cost_estimate_usd,
                      "trace": str(trace.path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
