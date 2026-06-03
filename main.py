"""CLI entrypoint for the agentic orchestrator.

Examples:
    # nominal run
    python main.py --input "Urgent: I was charged twice, refund me at john@acme.com"

    # failure run (every tool call fails)
    python main.py --input "login broken" --failure-rate 1.0

    # robustness run (perturbed input)
    python main.py --input "payment error, urgent" --perturb
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from agentic_orchestrator.config import Settings
from agentic_orchestrator.graph import run_once
from agentic_orchestrator.perturbation import perturb


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stateful agentic orchestrator (triage).")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="ticket text to process")
    src.add_argument("--input-file", help="path to a file containing the ticket text")
    p.add_argument("--failure-rate", type=float, help="override TOOL_FAILURE_RATE [0-1]")
    p.add_argument("--perturb", action="store_true", help="apply input perturbation")
    p.add_argument("--seed", type=int, default=0, help="RNG seed (failures/perturbation)")
    p.add_argument("--runs-dir", help="override RUNS_DIR")
    return p.parse_args(argv)


def _print_summary(state, trace_path) -> None:
    from agentic_orchestrator import llm

    mode = f"LLM ({llm.current_model()})" if llm.is_available() else "rule-based (offline)"
    print(f"\nrun_id: {state['run_id']}")
    print(f"mode:       {mode}")
    print("workflow:   planner -> executor -> verifier -> finalizer")
    print(f"\ninput:      {state.get('input_text')!r}")
    print(f"sanitized:  {state.get('sanitized_text')!r}")

    clf = state.get("classification")
    if clf:
        line = f"classified: {clf['category']} (priority={clf['priority']}, source={clf.get('source')}"
        if "confidence" in clf:
            line += f", confidence={clf['confidence']}"
        print(line + ")")
        if clf.get("reason"):
            print(f"  reason:   {clf['reason']}")

    for ev in state.get("guardrail_events", []):
        print(f"guardrail:  [{ev['kind']}] {ev['detail']}")

    for err in state.get("errors", []):
        print(f"error:      {err}")

    llm_calls = state.get("llm_calls", [])
    if llm_calls:
        tokens = sum(c["input_tokens"] + c["output_tokens"] for c in llm_calls)
        print(f"\nllm_calls:  {len(llm_calls)} ({tokens} tokens total)")

    decision = state.get("decision", {})
    print(f"decision:   {decision.get('outcome', '?').upper()} — {decision.get('reason')}")
    print(f"trace:      {trace_path}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.input_file:
        with open(args.input_file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = args.input

    if args.perturb:
        text = perturb(text, seed=args.seed)

    settings = Settings.from_env()
    overrides = {}
    if args.failure_rate is not None:
        overrides["tool_failure_rate"] = args.failure_rate
    if args.runs_dir is not None:
        from pathlib import Path

        overrides["runs_dir"] = Path(args.runs_dir)
    if overrides:
        settings = dataclasses.replace(settings, **overrides)

    state, trace_path = run_once(text, settings, seed=args.seed)
    _print_summary(state, trace_path)

    return 0 if state.get("decision", {}).get("outcome") == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
