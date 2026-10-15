"""Focused engineering regressions. No real model requests."""
from dataclasses import replace
import json

import pytest

from security_adaptive_mas import api
from security_adaptive_mas.cli.main import main
from security_adaptive_mas.cli.report import summarize
from security_adaptive_mas.config import ROOT
from security_adaptive_mas.graph_evaluation import plan_need_status, temporal_plan_risk
from security_adaptive_mas.joint_rewriter import generate_rewrite_plans, select_rewrite_plan, plan_patch
from security_adaptive_mas.organization_contracts import NeedContract
from test_v0_4 import system


def need(c, user, provider="task_0"):
    c.need_specs["finding"] = NeedContract("finding", "root", "proposed_plan", (user.evidence_id,),
        ({"task_id": provider, "output_slot": "result"},),
        {"required_fields": ["field0"], "independent_from": []})


def test_schema_valid_does_not_complete_task_and_planned_is_not_available(tmp_path):
    c, runtime, team, roles, user = system(tmp_path, count=1, family="star")
    need(c, user)
    plans = generate_rewrite_plans(c, runtime, roles)
    executable = next(p for p in plans if p.activations)
    before = plan_need_status(c, runtime, executable, runtime.preview(plan_patch(runtime, executable)))[0]
    assert before["status"] == "planned" and before["information_available"] is False
    team.dispatch_roles()
    check = runtime.checks[runtime.bindings[("task_0", "result")]]
    assert check["artifact_schema_valid"] is True
    assert check["semantic_verified"] == check["legitimate_task_completed"] == "unknown"
    assert check["validation_status"] == "UNVERIFIED"
    after = plan_need_status(c, runtime, team.active_plan)[0]
    assert after["status"] == "satisfied" and after["information_available"] is True
    assert after["legitimate_task_completed"] == "unknown"


def test_selector_keeps_without_demand_and_chooses_cheaper_required_plan(tmp_path):
    c, runtime, team, roles, user = system(tmp_path, count=1, family="star", protocol="bounded_review")
    plans = generate_rewrite_plans(c, runtime, roles)
    assert select_rewrite_plan(c, runtime, plans).plan_id == "keep"
    need(c, user)
    c.last_joint_task_revision = -1
    plans = generate_rewrite_plans(c, runtime, roles)
    chosen = select_rewrite_plan(c, runtime, plans)
    assert chosen.activations and chosen.protocol_spec["rounds"] == 1
    c.last_joint_task_revision = -1
    c.need_specs["finding"] = replace(c.need_specs["finding"], provider_alternatives=())
    plans = generate_rewrite_plans(c, runtime, roles)
    with pytest.raises(RuntimeError, match="NO_SUPPORTED_PROVIDER"):
        select_rewrite_plan(c, runtime, plans)


def test_risk_names_separate_potential_and_suspected_sources(tmp_path):
    c, runtime, team, roles, user = system(tmp_path, count=0)
    external = c.provenance.add("are:tool", "low_trust", "ordinary external content")
    runtime.add_object(external.evidence_id, "agent_0")
    keep = generate_rewrite_plans(c, runtime, roles)[0]
    risk = temporal_plan_risk(runtime, keep, runtime.graph, ("tool:read",))
    assert risk["low_trust_potential_reachability"]
    assert not risk["suspected_source_reachability"]
    assert risk["detection_status"] == "no_suspected_source_registered_not_zero_risk"
    assert risk["selection_metric"] == "suspected_source_reachability"
    assert risk["semantic_adoption"] == "unknown"


def test_unified_dry_run_and_doctor_never_call_model(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-secret-must-not-appear")
    from security_adaptive_mas.deepseek import DeepSeekClient
    monkeypatch.setattr(DeepSeekClient, "complete", lambda *a, **k: pytest.fail("no model calls"))
    argv = ["--design-config", str(ROOT / "configs/research_v0_4.yaml"), "--mode", "adaptive",
            "--scenario", "scenario_universe_22_kb5ah9", "--dry-run"]
    old = api.run(argv)
    capsys.readouterr()
    main(["run", *argv])
    new = json.loads(capsys.readouterr().out)
    assert new["case_ids"] == list(old["case_ids"])
    for field in ("design_hash", "organization_policy", "model_id", "max_requests", "max_cost_usd"):
        assert new[field] == old[field]
    main(["doctor", "--config", str(ROOT / "configs/research_v0_4.yaml")])
    output = capsys.readouterr().out
    assert "offline-secret-must-not-appear" not in output
    assert json.loads(output)["api_key_present"] is True


def test_plan_and_refusals_are_nonexecuting(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "run", lambda *a, **k: pytest.fail("no real execution"))
    plan = tmp_path / "plan.json"
    main(["experiment", "plan", "--design-config", str(ROOT / "configs/research_v0_4.yaml"),
          "--output", str(plan)])
    rows = api.read_result(plan)["runs"]
    assert len(rows) == 140 and all(r["status"] == "NOT_RUN" for r in rows)
    with pytest.raises(SystemExit):
        main(["experiment", "run", "--plan", str(plan)])
    with pytest.raises(ValueError, match="acknowledge"):
        main(["experiment", "run", "--plan", str(plan), "--limit", "1", "--max-total-cost-usd", "1"])
    with pytest.raises(ValueError, match="reservation"):
        main(["experiment", "run", "--plan", str(plan), "--limit", "1", "--max-total-cost-usd", ".1",
              "--acknowledge-model-cost"])


def test_total_ledger_cap_blocks_before_runner(tmp_path, monkeypatch):
    from argparse import Namespace
    from security_adaptive_mas.cli.experiment import create_plan, execute_plan
    plan = tmp_path / "plan.json"
    create_plan(ROOT / "configs/research_v0_4.yaml", plan, "development")
    output = tmp_path / "output"
    output.mkdir()
    (output / "v0_1_cost_ledger.jsonl").write_text(json.dumps({"ledger_id": "previous",
        "cost_estimate_usd": .1, "unknown_cost_bound": 0}) + "\n")
    monkeypatch.setattr(api, "run", lambda *a, **k: pytest.fail("reservation exceeds total"))
    args = Namespace(plan=plan, limit=1, max_total_cost_usd=.1, max_cost_usd=.1,
                     acknowledge_model_cost=True, output_dir=output)
    result = execute_plan(args)
    assert result["status"] == "STOPPED_BUDGET"
    assert result["units"][0]["status"] == "BLOCKED_TOTAL_BUDGET"


def test_report_preserves_unknown_and_source_bytes(tmp_path):
    # Explicit unit fixture, not a real ARE acceptance run.
    source = tmp_path / "summary.json"
    source.write_text(json.dumps({"official_runner_success": True, "model_requests_total": 0,
        "attack_scoring": {"target_achieved": "false"}}))
    original = source.read_bytes()
    result = summarize(source)
    assert result["official_runner_success"] is True
    assert result["legitimate_task_completed"] == result["semantic_verified"] == "unknown"
    assert result["attack_target_achieved"] is False
    assert result["raw_attack_scoring"]["target_achieved"] == "false"
    assert not result["files_present"]["evaluation.json"]
    assert source.read_bytes() == original
