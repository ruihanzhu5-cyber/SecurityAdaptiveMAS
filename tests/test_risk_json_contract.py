"""Regression test for Issue #3; no network/API calls."""
import json
from types import SimpleNamespace

from security_adaptive_mas.risk_monitor import RiskMonitor, RISK_PROMPT_VERSION


def test_risk_monitor_json_mode_prompt_contract():
    seen = []

    class FakeClient:
        def complete(self, messages, **kwargs):
            seen.append((messages, kwargs))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({"suspicion": "not_supported", "reason_evidence_ids": [],
                                    "possible_goal_deviation": "No deviation observed"})))])

    class FakeTrace:
        def log(self, *args, **kwargs):
            pass

    provenance = SimpleNamespace(items={"ev_1": SimpleNamespace(text="ordinary email body")})
    monitor = RiskMonitor(FakeClient(), provenance, lambda: "Summarize emails", FakeTrace(), "semantic")
    row = monitor.assess("worker_1", ("ev_1",))
    assert row["suspicion"] == "not_supported"
    assert row["model_and_prompt_version"] == RISK_PROMPT_VERSION
    assert len(seen) == 1
    messages, kwargs = seen[0]
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["purpose"] == "risk_assessment"
    assert any("json" in m["content"].lower() for m in messages)
    assert "JSON object" in messages[0]["content"]
    assert set(json.loads(messages[1]["content"])) == {"goal", "proposal", "external_evidence"}
    assert kwargs["instance_id"] == "risk_monitor"
