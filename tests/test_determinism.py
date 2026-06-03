"""Determinism guarantee: same input + seed (offline) -> identical trace.

The orchestrator's reproducibility claim hinges on this. We strip volatile
fields (timestamps, wall-clock durations) and assert the remainder is
bit-identical across two independent runs.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from agentic_orchestrator.config import Settings
from agentic_orchestrator.graph import run_once

_VOLATILE = {
    "started_at",
    "ended_at",
    "duration_ms",
    "total_duration_ms",
    "run_id",
}


def _canonical(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _canonical(v) for k, v in obj.items() if k not in _VOLATILE}
    if isinstance(obj, list):
        return [_canonical(x) for x in obj]
    return obj


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")


def _run(tmp_path, name: str, *, seed: int, failure_rate: float = 0.0):
    settings = dataclasses.replace(
        Settings.from_env(),
        runs_dir=tmp_path / name,
        tool_failure_rate=failure_rate,
    )
    state, trace_path = run_once(
        "Urgent: charged twice, refund to john@acme.com",
        settings,
        seed=seed,
        run_id=f"det-{name}",
    )
    return json.loads(trace_path.read_text())


def test_same_seed_same_trace(tmp_path):
    a = _canonical(_run(tmp_path, "a", seed=42))
    b = _canonical(_run(tmp_path, "b", seed=42))
    assert a == b


def test_same_seed_under_fault_is_deterministic(tmp_path):
    a = _canonical(_run(tmp_path, "fa", seed=7, failure_rate=0.5))
    b = _canonical(_run(tmp_path, "fb", seed=7, failure_rate=0.5))
    assert a == b


def test_different_seed_under_fault_can_differ(tmp_path):
    # Sanity: the seed must actually drive variability when failures are stochastic.
    a = _canonical(_run(tmp_path, "s1", seed=1, failure_rate=0.5))
    b = _canonical(_run(tmp_path, "s2", seed=2, failure_rate=0.5))
    # At minimum the tool_calls or errors should differ across seeds; if both
    # are identical the seed isn't being used for fault sampling.
    assert (a["tool_calls"] != b["tool_calls"]) or (a["errors"] != b["errors"])
