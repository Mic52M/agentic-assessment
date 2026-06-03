import dataclasses
import json

import pytest

from agentic_orchestrator.config import Settings
from agentic_orchestrator.graph import run_once


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # Force the deterministic rule-based path so tests are hermetic and never
    # hit the real API, regardless of the developer's environment.
    monkeypatch.setenv("LLM_ENABLED", "false")


def _settings(tmp_path):
    return dataclasses.replace(Settings.from_env(), runs_dir=tmp_path)


def test_nominal_run_is_accepted(tmp_path):
    state, trace_path = run_once(
        "Urgent: charged twice, refund to john@acme.com",
        _settings(tmp_path),
    )
    assert state["decision"]["outcome"] == "accepted"
    assert state["classification"]["category"] == "billing"
    assert state["classification"]["source"] == "rules"  # offline fallback
    # PII was redacted before classification.
    assert "john@acme.com" not in state["sanitized_text"]
    assert any(e["kind"] == "pii_redaction" for e in state["guardrail_events"])
    assert trace_path.exists()


def test_failure_run_is_rejected_and_traced(tmp_path):
    settings = dataclasses.replace(_settings(tmp_path), tool_failure_rate=1.0)
    state, trace_path = run_once("login broken", settings, seed=1)
    assert state["decision"]["outcome"] == "rejected"
    assert state["errors"], "tool failures should be recorded"
    # Every tool call in this run failed.
    assert all(not c["ok"] for c in state["tool_calls"])


def test_trace_record_is_complete(tmp_path):
    state, trace_path = run_once("payment error", _settings(tmp_path))
    record = json.loads(trace_path.read_text())
    node_names = [n["node"] for n in record["nodes"]]
    assert node_names == ["planner", "executor", "verifier", "finalizer"]
    for node in record["nodes"]:
        assert "duration_ms" in node
        assert "state_before" in node and "output" in node
    assert record["decision"] is not None
    assert record["total_duration_ms"] >= 0


def test_events_jsonl_is_appended(tmp_path):
    run_once("login broken, urgent", _settings(tmp_path))
    events_file = tmp_path / "events.jsonl"
    lines = events_file.read_text().strip().splitlines()
    assert len(lines) == 4  # one per node
    assert all(json.loads(line)["run_id"] for line in lines)
