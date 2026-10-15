"""Free checks for the explicitly authorized one-episode budget exception."""
import hashlib
import json

import pytest

from security_adaptive_mas import run_framework as runner


@pytest.fixture
def authorized(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    ledger = tmp_path / "history/v0_1_cost_ledger.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(json.dumps({"ledger_id": "old_pending", "cost_estimate_usd": .4,
                                  "unknown_cost_bound": 0}) + "\n", encoding="utf-8")
    monkeypatch.setattr(runner, "C1_LEDGER", ledger)
    monkeypatch.setattr(runner, "code_state", lambda: {"code_revision": "free_fixture"})
    path = tmp_path / "results/authorization.json"
    args = runner.build_parser().parse_args(["--scenario", "scenario_universe_22_0421h2",
        "--max-requests", "200", "--max-total-tokens", "2000000", "--max-cost-usd", "2",
        "--batch-cap-usd", str(runner.C1_STUDY_CAP), "--output-dir", str(ledger.parent),
        "--c1-b1-authorization", str(path)])
    args.design = {"method": "modular_risk_aware", "model": "deepseek-flash"}
    args.design_hash = "fixture_design"
    runner.save(path, {"kind": "v0_6_C1_B1_single_validation", "episode_limit": 1,
        "code": runner.code_state(), "design_hash": args.design_hash,
        "configuration": {"scenario": args.scenario, "method": args.design["method"],
            "attack": "none", "model": "deepseek-flash", "max_turns": 20, "max_requests": 200,
            "max_total_tokens": 2000000, "max_cost_usd": 2.0, "idle_wait_seconds": None},
        "shared_ledger": str(ledger), "absolute_cap_usd": runner.C1_STUDY_CAP,
        "ledger_sha256_before": hashlib.sha256(ledger.read_bytes()).hexdigest(), "frozen_input_hashes": {}})
    return args, ledger


def test_default_budget_remains_unchanged():
    args = runner.build_parser().parse_args(["--scenario", "other"])
    assert (args.max_requests, args.max_total_tokens, args.max_cost_usd) == (100, 800000, .4)
    assert runner.c1_authorization(args) is None
    assert runner.approved_study_cap(args) == 1.6


def test_raised_limits_without_authorization_are_rejected(monkeypatch):
    monkeypatch.setattr(runner, "load_design", lambda *args: {"upstream_lock": "lock"})
    with pytest.raises(SystemExit) as caught:
        runner.main(["--scenario", "scenario_universe_22_0421h2", "--max-cost-usd", "2", "--dry-run"])
    assert caught.value.code == 2


@pytest.mark.parametrize("field,value,reason", [("requests", 200, "request"),
    ("input_tokens", 2000000, "token"), ("peak_cost_estimate_usd", 2.0, "cost"),
    ("unknown_cost_bound", 2.0, "cost")])
def test_shared_budget_still_rejects_before_submission(tmp_path, field, value, reason):
    from security_adaptive_mas.deepseek import RunBudget, BudgetExceeded
    from security_adaptive_mas.trace import TraceLogger
    budget = RunBudget(TraceLogger(tmp_path / "trace.jsonl"), 200, 2000000, 2.0)
    budget.before_request([{"role": "user", "content": "free"}], 4096, purpose="coordinator")
    budget.after_response(type("Usage", (), {"prompt_tokens": 10, "completion_tokens": 10})(), 0,
                          purpose="coordinator")
    setattr(budget, field, value)
    with pytest.raises(BudgetExceeded, match=reason + " budget exhausted"):
        budget.before_request([{"role": "user", "content": "free"}], 4096, purpose="benchmark_judge")


@pytest.mark.parametrize("field,value", [("scenario", "other"), ("attack", "single_injection"),
    ("max_requests", 201), ("max_cost_usd", 2.01), ("idle_wait_seconds", 120),
    ("study_unit", "C1_B0")])
def test_authorization_rejects_changed_scope(authorized, field, value):
    args, _ = authorized
    setattr(args, field, value)
    with pytest.raises(ValueError, match="scope_mismatch"):
        runner.c1_authorization(args)


def test_authorization_keeps_pending_charge_and_is_single_use(authorized):
    args, ledger = authorized
    authorization = runner.c1_authorization(args)
    assert runner.ledger_charge(ledger) == .4
    from pathlib import Path
    with Path(authorization["claim"]).open("x", encoding="utf-8") as stream:
        stream.write("free fixture claim")
    with pytest.raises(ValueError, match="already_attempted"):
        runner.c1_authorization(args)
    assert runner.ledger_charge(ledger) == .4


def test_authorization_rejects_changed_history(authorized):
    args, ledger = authorized
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="ledger_changed"):
        runner.c1_authorization(args)
