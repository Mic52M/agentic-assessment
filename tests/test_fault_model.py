"""Tests for the extended fault model introduced in Fase 3."""

from __future__ import annotations

import dataclasses
import random
import time

import pytest

from agentic_orchestrator.config import Settings
from agentic_orchestrator.graph import run_once
from agentic_orchestrator.tools.failure import (
    FaultModel,
    maybe_crash,
    maybe_delay,
    should_corrupt,
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")


# --- unit-level fault helpers ---------------------------------------------

def test_maybe_crash_rate_zero_never_raises():
    rng = random.Random(0)
    for _ in range(20):
        maybe_crash("t", FaultModel(), rng)  # must not raise


def test_maybe_crash_rate_one_always_raises():
    from agentic_orchestrator.tools.failure import ToolFailure
    rng = random.Random(0)
    with pytest.raises(ToolFailure):
        maybe_crash("t", FaultModel(crash_rate=1.0), rng)


def test_maybe_delay_sleeps_when_slow_fires():
    model = FaultModel(slow_rate=1.0, slow_ms=10.0)
    t0 = time.perf_counter()
    delay = maybe_delay(model, random.Random(0))
    elapsed = (time.perf_counter() - t0) * 1000
    assert delay == 10.0
    assert elapsed >= 9.0  # allow a bit of slack


def test_maybe_delay_zero_rate_does_not_sleep():
    model = FaultModel(slow_rate=0.0, slow_ms=100.0)
    t0 = time.perf_counter()
    maybe_delay(model, random.Random(0))
    assert (time.perf_counter() - t0) * 1000 < 5


def test_should_corrupt_rate_one_always_true():
    assert should_corrupt(FaultModel(corrupt_rate=1.0), random.Random(0)) is True


def test_should_corrupt_rate_zero_always_false():
    assert should_corrupt(FaultModel(corrupt_rate=0.0), random.Random(0)) is False


# --- integration via the graph --------------------------------------------

def _settings(tmp_path, **kw):
    base = dataclasses.replace(Settings.from_env(), runs_dir=tmp_path)
    return dataclasses.replace(base, **kw)


def test_slow_fault_increases_latency(tmp_path):
    settings = _settings(tmp_path, tool_slow_rate=1.0, tool_slow_ms=30.0)
    state, _ = run_once("billing refund issue", settings, seed=0)
    # The executor's tool call gets the slow injection at least once.
    durations = [c["duration_ms"] for c in state["tool_calls"]]
    assert any(d >= 25 for d in durations), f"expected a slow call, got {durations}"


def test_corrupt_fault_changes_category_and_keeps_classification(tmp_path):
    settings = _settings(tmp_path, tool_corrupt_rate=1.0)
    state, _ = run_once("billing refund issue", settings, seed=0)
    clf = state["classification"]
    # Offline rule classifier would normally pick 'billing' — corruption must
    # leave the classification structurally intact but with a *different*
    # category, and the source tag must show the corruption explicitly.
    assert clf["category"] != "billing"
    assert "corrupted" in clf["source"]


def test_offline_verifier_does_not_catch_corruption(tmp_path):
    """Important *expected* result for the paper: a structural verifier is
    blind to semantic corruption. The decision still gets accepted."""
    settings = _settings(tmp_path, tool_corrupt_rate=1.0)
    state, _ = run_once("billing refund issue", settings, seed=0)
    assert state["decision"]["outcome"] == "accepted"
