"""Runtime configuration, loaded from environment / .env.

Kept deliberately small: a frozen dataclass populated from os.environ.
No pydantic dependency — the surface is tiny and fully transparent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Config keys this app reads from the environment / .env.
_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "LLM_ENABLED",
    "LLM_MODEL",
    "OTEL_ENABLED",
    "RUNS_DIR",
    "LOG_LEVEL",
    "TOOL_FAILURE_RATE",
    "TOOL_SLOW_RATE",
    "TOOL_SLOW_MS",
    "TOOL_CORRUPT_RATE",
)

try:
    from dotenv import load_dotenv

    # A real env var should win over .env (12-factor), but an *empty* one
    # (common in some sandboxes/CI) must not silently shadow a valid .env value.
    for _k in _ENV_KEYS:
        if os.environ.get(_k) == "":
            del os.environ[_k]
    load_dotenv()
except ImportError:  # python-dotenv is optional; env vars still work.
    pass


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


_DEFAULT_MODEL = "claude-opus-4-7"


@dataclass(frozen=True)
class Settings:
    runs_dir: Path = Path("runs")
    log_level: str = "INFO"
    llm_enabled: bool = True
    llm_model: str = _DEFAULT_MODEL
    otel_enabled: bool = False
    tool_failure_rate: float = 0.0  # crash family
    tool_slow_rate: float = 0.0
    tool_slow_ms: float = 0.0
    tool_corrupt_rate: float = 0.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            runs_dir=Path(os.getenv("RUNS_DIR", "runs")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            llm_enabled=_as_bool(os.getenv("LLM_ENABLED"), False),
            llm_model=os.getenv("LLM_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL,
            otel_enabled=_as_bool(os.getenv("OTEL_ENABLED"), False),
            tool_failure_rate=_as_float(os.getenv("TOOL_FAILURE_RATE"), 0.0),
            tool_slow_rate=_as_float(os.getenv("TOOL_SLOW_RATE"), 0.0),
            tool_slow_ms=_as_float(os.getenv("TOOL_SLOW_MS"), 0.0),
            tool_corrupt_rate=_as_float(os.getenv("TOOL_CORRUPT_RATE"), 0.0),
        )

    def fault_model(self):
        """Build a FaultModel from the four tool_* fields.

        Imported lazily so config.py doesn't pull tools/ into its imports.
        """
        from .tools.failure import FaultModel

        return FaultModel(
            crash_rate=self.tool_failure_rate,
            slow_rate=self.tool_slow_rate,
            slow_ms=self.tool_slow_ms,
            corrupt_rate=self.tool_corrupt_rate,
        )
