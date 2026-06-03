"""Fault injection helpers — three independent families.

Each family models a distinct way an external dependency can misbehave:

  * ``crash``    — the call raises (`ToolFailure`). The current node records
                    the failure and continues degraded.
  * ``slow``     — the call returns successfully but with extra latency.
                    Used to drive availability/latency studies under stress.
  * ``corrupt``  — the call returns a *syntactically valid but semantically
                    wrong* result. Used to test whether the verifier catches
                    silent failures.

A ``FaultModel`` carries the three rates and the slow latency. The helpers
``maybe_crash`` / ``maybe_delay`` / ``should_corrupt`` are seed-driven so
campaigns are reproducible.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass


class ToolFailure(RuntimeError):
    """Injected failure representing an unavailable/erroring tool."""


@dataclass(frozen=True)
class FaultModel:
    crash_rate: float = 0.0
    slow_rate: float = 0.0
    slow_ms: float = 0.0  # latency added when slow fires (per affected call)
    corrupt_rate: float = 0.0

    @classmethod
    def crash_only(cls, rate: float) -> "FaultModel":
        return cls(crash_rate=rate)


_NO_FAULT = FaultModel()


def maybe_crash(tool_name: str, model: FaultModel, rng: random.Random) -> None:
    if model.crash_rate <= 0.0:
        return
    if rng.random() < model.crash_rate:
        raise ToolFailure(f"injected failure in tool '{tool_name}'")


def maybe_delay(model: FaultModel, rng: random.Random) -> float:
    """If the slow family fires, sleep ``model.slow_ms`` and return the delay."""
    if model.slow_rate <= 0.0 or model.slow_ms <= 0.0:
        return 0.0
    if rng.random() < model.slow_rate:
        time.sleep(model.slow_ms / 1000.0)
        return model.slow_ms
    return 0.0


def should_corrupt(model: FaultModel, rng: random.Random) -> bool:
    if model.corrupt_rate <= 0.0:
        return False
    return rng.random() < model.corrupt_rate


# --- Backward-compat shim --------------------------------------------------
# `maybe_fail(tool, rate, rng)` is still used by the planner (which only
# needs the crash family). Keep it as a thin wrapper to avoid touching that
# node when the fault model grows.


def maybe_fail(tool_name: str, failure_rate: float, rng: random.Random) -> None:
    maybe_crash(tool_name, FaultModel(crash_rate=failure_rate), rng)
