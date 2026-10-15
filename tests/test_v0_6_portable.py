"""Portable v0.6 contracts. No ARE installation, dataset or model API required."""
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from security_adaptive_mas.are_adapter import benchmark_evaluation
from security_adaptive_mas.config import ROOT


@pytest.mark.parametrize("turn,last_success,raw,interrupted,expected", [
    (None, True, True, False, "unknown"),
    (0, True, False, False, "unknown"),
    (1, True, True, False, True),
    (0, False, False, False, False),
    (1, True, True, True, "unknown"),
])
def test_returned_runner_is_not_automatically_task_success(turn, last_success, raw, interrupted, expected):
    judge = None if turn is None else SimpleNamespace(state=SimpleNamespace(
        initialized=True, turn_idx=turn, nb_turns=2, last_turn_success=last_success))
    value = benchmark_evaluation(SimpleNamespace(judge=judge),
        SimpleNamespace(success=raw, exception=None), interrupted=interrupted)
    assert value["official_runner_returned"]
    assert value["legitimate_task_completed"] == expected
    assert value["benchmark_judge_completed"] is (expected != "unknown")


def test_v06_portable_configuration_does_not_claim_missing_upstreams(tmp_path, monkeypatch):
    from security_adaptive_mas.orchestration import configuration
    config_path = tmp_path / "configs/research_v0_6.yaml"
    config_path.parent.mkdir()
    shutil.copyfile(ROOT / "configs/research_v0_6.yaml", config_path)
    monkeypatch.setattr(configuration, "ROOT", tmp_path)
    design = configuration.load_design(config_path)
    assert design["version"] == "v0.6" and design["paths"]["are_root"] is None
    with pytest.raises(FileNotFoundError, match="BLOCKED_explicit_path_missing"):
        configuration.project_config(design)
    with pytest.raises(ValueError, match="legacy design configurations"):
        configuration.load_design(config_path, override={"version": "v0.4"})


def test_cli_help_does_not_import_are_or_call_models():
    result = subprocess.run([sys.executable, "-c",
        "import sys; from security_adaptive_mas.cli.main import main; main(['--help']); assert 'are' not in sys.modules"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), "DEEPSEEK_API_KEY": ""},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "ARE/Gaia2 research harness" in result.stdout


@pytest.mark.parametrize("status", ["INDETERMINATE_IDLE", "BLOCKED_EVALUATION"])
def test_study_stops_after_unscored_episode_without_exception(tmp_path, monkeypatch, status):
    from scripts import run_v0_6_five_case_study as study
    frozen = {"fixture": "portable"}
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('', encoding="utf-8")
    manifest = {"status": "PREPARED", "code": frozen, "shared_ledger": str(ledger),
        "absolute_cap_usd": 1.6, "units": [{"unit_id": "first", "status": "READY"},
                                         {"unit_id": "second", "status": "READY"}]}
    (tmp_path / "study_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(study, "code_state", lambda: frozen)
    monkeypatch.setattr(study, "command", lambda *args: [])
    calls = []
    summary = dict.fromkeys(["exception_type", "stop_reason", "official_runner_success", "legitimate_task_completed",
        "model_requests_submitted", "successful_model_responses", "input_tokens_total", "output_tokens_total",
        "cost_estimate_usd", "unknown_cost_bound", "wall_time_seconds", "effective_graph_patches"])
    summary.update(run_id="free_fixture", scenario_id="fixture", run_status=status)
    def run(argv):
        calls.append(argv)
        return {"summaries": [summary], "batch_dir": str(tmp_path)}
    monkeypatch.setattr(study, "run", run)
    result = study.execute(tmp_path)
    assert len(calls) == 1
    assert result["units"][0]["status"] == status
    assert result["units"][1]["status"] == "NOT_RUN"
    assert result["status"] == "STOPPED_SYSTEMIC_FAILURE"
