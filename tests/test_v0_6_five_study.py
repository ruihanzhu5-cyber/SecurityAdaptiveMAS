"""Offline frozen-study budget, readiness and stop-gate checks."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import pytest
from scripts import run_v0_6_five_case_study as study
from security_adaptive_mas import run_framework as framework
from security_adaptive_mas.orchestration.configuration import load_design,design_hash
from security_adaptive_mas.config import ROOT


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    matrix=json.loads((ROOT/"work/five_preflight_verified/compatibility_matrix.json").read_text(encoding="utf-8"))
    fake=tmp_path/"checkout";(fake/"configs").mkdir(parents=True);(fake/"tests").mkdir();(fake/"work").mkdir()
    shutil.copyfile(ROOT/"configs/research_v0_6.yaml",fake/"configs/research_v0_6.yaml")
    shutil.copyfile(ROOT/"configs/upstream_v0_6.lock.json",fake/"configs/upstream_v0_6.lock.json")
    shutil.copyfile(ROOT/"work/v0_6_local_paths.yaml",fake/"work/v0_6_local_paths.yaml")
    monkeypatch.setattr(study,"ROOT",fake)
    frozen={"dirty_status":"","source_file_hashes":{},"code_revision":"offline_fixture"}
    monkeypatch.setattr(study,"code_state",lambda:frozen)
    monkeypatch.setattr(study,"preflight",lambda p:matrix)
    calls=[]
    def dry(argv):
        assert "--dry-run" in argv;calls.append(argv)
        return {"design_hash":design_hash(load_design(Path(argv[1])))}
    monkeypatch.setattr(study,"run",dry)
    ledger=tmp_path/"history/v0_1_cost_ledger.jsonl";ledger.parent.mkdir();ledger.write_text('{"ledger_id":"old","cost_estimate_usd":0.79,"unknown_cost_bound":0.01}\n')
    directory=fake/"results/study";m=study.prepare(directory,ledger)
    return directory,ledger,m,calls,monkeypatch,frozen


def test_twenty_units_dry_checks_same_attacks_and_retained_ledger(prepared):
    directory,ledger,m,calls,_,_=prepared
    assert len(m["units"])==len(calls)==20
    assert sum(u["status"]=="READY" for u in m["units"])==18
    assert m["ledger_charge_before"]==pytest.approx(.8) and m["absolute_cap_usd"]==pytest.approx(8.8)
    assert ledger.read_bytes()==(directory/"inputs/ledger_before.jsonl").read_bytes()
    for i in range(0,20,4):
        units=m["units"][i:i+4]
        assert [u["condition"] for u in units]==["B1","B0","A0","A1"]
        assert units[2]["attack_config"]==units[3]["attack_config"]
    blocked=[u for u in m["units"] if u["status"].startswith("BLOCKED")]
    assert len(blocked)==2 and all(u["case_id"]=="C3" and u["argv"] is None for u in blocked)
    assert all(u["legitimate_scope"]=="PARTIAL_SCOPE" for u in m["units"] if u["case_id"]=="C4")
    with pytest.raises(FileExistsError):study.prepare(directory,ledger)


def test_named_budget_is_frozen_and_default_not_expanded(prepared):
    directory,ledger,m,_,monkeypatch,frozen=prepared;unit=m["units"][0]
    monkeypatch.setattr(framework,"ROOT",directory.parents[1]);monkeypatch.setattr(framework,"code_state",lambda:frozen)
    args=framework.build_parser().parse_args(study.command(unit,m,directory/"study_manifest.json"))
    args.design=load_design(args.design_config);args.design_hash=design_hash(args.design)
    assert framework.approved_study_cap(args)==pytest.approx(8.8)
    args.batch_cap_usd=9
    with pytest.raises(ValueError,match="absolute_cap_changed"):framework.approved_study_cap(args)
    args.study_manifest=None;args.study_unit=None
    assert framework.approved_study_cap(args)==1.6


def test_systemic_failure_stops_ready_units_and_preserves_blocked(prepared):
    directory,_,m,_,monkeypatch,_=prepared
    def broken(argv):raise RuntimeError("offline_runner_failure")
    monkeypatch.setattr(study,"run",broken)
    result=study.execute(directory)
    assert result["status"]=="STOPPED_SYSTEMIC_FAILURE"
    assert result["units"][0]["status"]=="FAILED"
    assert sum(u["status"]=="NOT_RUN" for u in result["units"])==17
    assert sum(u["status"].startswith("BLOCKED") for u in result["units"])==2
    with pytest.raises(RuntimeError,match="already_attempted"):study.execute(directory)


def test_reservation_gate_blocks_without_model_call(prepared):
    directory,ledger,m,calls,_,_=prepared
    ledger.write_text('{"ledger_id":"old","cost_estimate_usd":8.5,"unknown_cost_bound":0}\n')
    calls.clear();result=study.execute(directory)
    assert not calls
    assert sum(u["status"]=="BLOCKED_BUDGET" for u in result["units"])==18


def test_official_single_agent_can_end_without_delegation(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from security_adaptive_mas.deepseek import DeepSeekClient,RunBudget
    from security_adaptive_mas.trace import TraceLogger
    from security_adaptive_mas.orchestration.session import ModularSession
    from security_adaptive_mas.orchestration.configuration import project_config
    from security_adaptive_mas.are_adapter import import_official_scenario,official_runner
    from security_adaptive_mas.adapters.are_modular import make_modular_agent_builder
    from are.simulation.scenarios.config import ScenarioRunnerConfig
    monkeypatch.setenv("DEEPSEEK_API_KEY","offline-fixture-key")
    design=load_design(ROOT/"configs/research_v0_6.yaml");config=project_config(design)
    trace=TraceLogger(tmp_path/"framework_trace.jsonl")
    def complete(**kwargs):
        if kwargs.get("response_format")=={"type":"json_object"}:
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10,completion_tokens=10),choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"suspicion":"not_supported","reason_evidence_ids":[],"possible_goal_deviation":"offline fixture"})))])
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10,completion_tokens=10),
            choices=[SimpleNamespace(message=SimpleNamespace(content='Thought: Offline lifecycle fixture\nAction: {"action":"AgentUserInterface__send_message_to_user","action_input":{"content":"Offline lifecycle only"}}'))])
    client=DeepSeekClient(RunBudget(trace,100,800000,.4),completion_fn=complete);session=ModularSession(design,trace,client)
    scenario=import_official_scenario(config.select("scenario_universe_22_kb5ah9"),config.are_root,filesystem=design["paths"]["gaia2_filesystem"],sandbox=tmp_path)
    runner_config=ScenarioRunnerConfig(agent="default",model="deepseek-flash",model_provider="deepseek",endpoint="https://api.deepseek.com",oracle=False,export=False,max_turns=1,output_dir=str(tmp_path/"official"))
    result=official_runner(make_modular_agent_builder(session,client)).run(runner_config,scenario)
    assert result.exception is None and session.terminal_error is None
    assert session.worker_steps==0 and session.work_items=={} and session.runtime.applied==0


def test_context_analysis_separates_prepared_submitted_and_real_usage(tmp_path):
    from scripts.analyze_v0_6_context_growth import analyze_episode
    io=tmp_path/"model_io";io.mkdir()
    request={"messages":[{"role":"user","content":"abc"}],"tools":None,"response_format":None,"instance_id":"agent_0","purpose":"coordinator","components":[{"object_id":"ev_1","version":1,"view_id":"full"}]}
    for pid in ["sent","rejected"]:(io/(pid+"_request.json")).write_text(json.dumps(request),encoding="utf-8")
    (io/"sent_0_response.json").write_text(json.dumps({"usage":{"prompt_tokens":2,"completion_tokens":1}}),encoding="utf-8")
    summary={"run_id":"fixture","design":{"rates":{"input_usd_per_token":.3,"output_usd_per_token":1.2}},"actual_usage_by_purpose":{},"exception_type":"BudgetExceeded","stop_reason":"budget_exhausted","official_runner_success":True,"legitimate_task_completed":"unknown","model_requests_submitted":1,"successful_model_responses":1,"requests_by_purpose":{"coordinator":1},"input_tokens_total":2,"output_tokens_total":1,"cost_estimate_usd":1.8,"unknown_cost_bound":0,"wall_time_seconds":1,"distinct_agent_instances":["agent_0"],"worker_steps":0,"effective_graph_patches":0}
    (tmp_path/"summary.json").write_text(json.dumps(summary),encoding="utf-8")
    events=[{"kind":"model_input_prepared","prepared_id":"sent","time_utc":"2026-10-15T00:00:00Z"},{"kind":"model_request_submitted","request_id":"sent:0"},{"kind":"model_response_received","request_id":"sent:0","elapsed_seconds":.2},{"kind":"model_input_prepared","prepared_id":"rejected","time_utc":"2026-10-15T00:00:01Z"},{"kind":"preflight_budget_rejection","reason":"fixture"}]
    (tmp_path/"framework_trace.jsonl").write_text("\n".join(json.dumps(e) for e in events),encoding="utf-8")
    analyze_episode(tmp_path)
    rows=json.loads((tmp_path/"context_growth.json").read_text())["requests"]
    assert rows[0]["submitted"] and rows[0]["actual_input_tokens"]==2
    assert not rows[1]["submitted"] and rows[1]["actual_input_tokens"]=="unknown"
    assert rows[1]["exact_duplicate_input"] and rows[1]["repeated_refs_unknown_byte_length"]==1
    assert rows[0]["role_text_utf8_bytes"]["user"]==3 and rows[0]["bytes_are_tokens"] is False
