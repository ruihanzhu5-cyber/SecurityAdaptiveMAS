"""Offline study lifecycle tests; no actual model requests."""
import json
import hashlib
from pathlib import Path

import pytest

from scripts import run_v0_6_pilot as pilot


@pytest.fixture
def isolated_pilot(tmp_path, monkeypatch):
    base=tmp_path/"shared_pilot"
    base.mkdir()
    monkeypatch.setattr(pilot,"BASE",base)
    relative="security_adaptive_mas/risk_monitor.py"
    frozen={"source_file_hashes":{relative:hashlib.sha256((pilot.ROOT/relative).read_bytes()).hexdigest()},"code_revision":"offline_fixture"}
    monkeypatch.setattr(pilot,"code_state",lambda:frozen)
    calls=[]
    def preview(argv):
        calls.append(argv)
        assert "--dry-run" in argv
        return {"design_hash":pilot.design_hash(pilot.load_design(Path(argv[1])))}
    monkeypatch.setattr(pilot,"run",preview)
    return base,calls,monkeypatch


def test_new_studies_keep_old_manifest_and_shared_ledger(isolated_pilot):
    base,calls,_=isolated_pilot
    old=b'{"status":"BLOCKED_STOPPED_AFTER_B1"}'
    (base/"experiment_manifest.json").write_bytes(old)
    ledger=b'{"ledger_id":"previous","cost_estimate_usd":0.002451,"unknown_cost_bound":0.018273}\n'
    (base/"v0_1_cost_ledger.jsonl").write_bytes(ledger)
    pilot.prepare("new_a")
    pilot.prepare("new_b")
    assert (base/"experiment_manifest.json").read_bytes()==old
    assert (base/"v0_1_cost_ledger.jsonl").read_bytes()==ledger
    assert len(calls)==8
    for study in ("new_a","new_b"):
        m=json.loads((base/"studies"/study/"experiment_manifest.json").read_text())
        assert m["shared_ledger"]==str(base/"v0_1_cost_ledger.jsonl")
        assert m["ledger_charge_usd_before_study"]==pytest.approx(0.020724)
        for unit in m["units"].values():
            argv=unit["argv"]
            assert argv[argv.index("--output-dir")+1]==str(base)
            assert argv[argv.index("--batch-cap-usd")+1]=="1.6"
            assert Path(unit["configuration_path"]).is_relative_to(base/"studies"/study)
    with pytest.raises(FileExistsError):pilot.prepare("new_a")
    with pytest.raises(FileExistsError):pilot.prepare()


def test_new_study_stops_after_first_integration_failure(isolated_pilot):
    base,_,monkeypatch=isolated_pilot
    pilot.prepare("new_run")
    attempts=[]
    def failure(argv):
        attempts.append(argv)
        raise RuntimeError("offline_integration_failure")
    monkeypatch.setattr(pilot,"run",failure)
    pilot.execute("new_run")
    m=json.loads((base/"studies/new_run/experiment_manifest.json").read_text())
    assert len(attempts)==1
    assert m["status"]=="BLOCKED_STOPPED_AFTER_B1"
    assert m["units"]["B1"]["error_type"]=="RuntimeError"
    assert all(m["units"][u]["status"]=="NOT_RUN" for u in ("B0","A0","A1"))
    with pytest.raises(RuntimeError,match="cannot be repeated"):
        pilot.execute("new_run")
    assert len(attempts)==1


def test_new_study_rejects_changed_frozen_code_before_call(isolated_pilot):
    _,calls,monkeypatch=isolated_pilot
    pilot.prepare("frozen_run")
    calls.clear()
    monkeypatch.setattr(pilot,"code_state",lambda:{"code_revision":"changed"})
    with pytest.raises(RuntimeError,match="frozen_working_tree_changed"):
        pilot.execute("frozen_run")
    assert not calls
