"""CLI: run an assessment campaign end-to-end.

    python -m assessment.run                 # full campaign (LLM if key set)
    python -m assessment.run --offline       # force the free rule-based path
    python -m assessment.run --limit 3 --seeds 1   # quick/cheap smoke campaign

Produces, under assessment_runs/:
    runs/            per-run orchestrator traces
    results.json     raw ExperimentResults
    metrics.json     computed metrics
    report.html      verdicts + metrics (open in a browser)
and prints the PASS/FAIL verdict table to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a non-functional assessment campaign.")
    p.add_argument("--seeds", type=int, default=3, help="repetitions per condition")
    p.add_argument("--limit", type=int, help="cap the number of tickets")
    p.add_argument("--offline", action="store_true", help="force rule-based (no API)")
    p.add_argument("--out", default="assessment_runs", help="output directory")
    p.add_argument(
        "--privacy-oracle",
        default="regex_strong",
        choices=["regex_strong", "presidio", "llm_judge"],
        help="independent detector used to measure residual PII leakage",
    )
    p.add_argument(
        "--bootstrap-iters", type=int, default=1000,
        help="bootstrap resamples for CI (0 = skip CI / comparisons)",
    )
    p.add_argument(
        "--bootstrap-seed", type=int, default=0,
        help="seed for bootstrap + permutation tests (reproducibility)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.offline:
        os.environ["LLM_ENABLED"] = "false"

    # Imported after the env override so llm picks up the offline switch.
    import dataclasses

    from agentic_orchestrator import llm
    from . import detectors
    from .harness import run_campaign
    from .metrics import compute_metric_stats, compute_metrics
    from .oracles import all_passed, evaluate
    from .report import render_html
    from .stats import standard_comparisons

    oracle_obj = detectors.get(args.privacy_oracle)
    if not oracle_obj.available():
        print(
            f"warning: privacy oracle {args.privacy_oracle!r} unavailable in this environment; "
            f"falling back to {detectors.DEFAULT_ORACLE!r}",
            file=sys.stderr,
        )
        args.privacy_oracle = detectors.DEFAULT_ORACLE

    mode = f"LLM ({llm.current_model()})" if llm.is_available() else "rule-based (offline)"
    print(
        f"running campaign — mode: {mode}, seeds={args.seeds}, limit={args.limit}, "
        f"privacy_oracle={args.privacy_oracle}"
    )

    out_dir = Path(args.out)
    results, campaign_dir = run_campaign(
        seeds=args.seeds, limit=args.limit, out_dir=out_dir,
        privacy_oracle=args.privacy_oracle,
    )

    if args.bootstrap_iters > 0:
        metric_stats = compute_metric_stats(
            results, n_bootstrap=args.bootstrap_iters, seed=args.bootstrap_seed,
        )
        verdicts = evaluate(metric_stats)
        comparisons = standard_comparisons(
            results, n_iter=args.bootstrap_iters, seed=args.bootstrap_seed,
        )
        metrics_for_json = {k: dataclasses.asdict(v) for k, v in metric_stats.items()}
    else:
        metric_stats = None
        verdicts = evaluate(compute_metrics(results))
        comparisons = []
        metrics_for_json = compute_metrics(results)

    payload = {"metrics": metrics_for_json,
               "comparisons": [dataclasses.asdict(c) for c in comparisons]}
    (campaign_dir / "metrics.json").write_text(json.dumps(payload, indent=2))
    report_path = render_html(
        results, verdicts, campaign_dir / "report.html",
        mode=mode, metric_stats=metric_stats, comparisons=comparisons,
    )

    print(f"\n{len(results)} runs · verdicts:")
    for v in verdicts:
        flag = "PASS" if v.passed else "FAIL"
        val = "n/a" if v.value is None else f"{v.value:g}"
        ci = "" if v.ci is None else f" [{v.ci[0]:g}, {v.ci[1]:g}]"
        print(f"  [{flag}] {v.spec.property:<12} {v.spec.name:<42} {val}{ci} ({v.spec.op} {v.spec.threshold:g})")
    if comparisons:
        print("\ncomparisons:")
        for c in comparisons:
            print(f"  {c.name:<40} Δ={c.delta:+g}  p={c.p_value:g}  (n={c.n})")

    ok = all_passed(verdicts)
    print(f"\noverall: {'ALL PROPERTIES PASS' if ok else 'SOME PROPERTIES FAIL'}")
    print(f"report:  {report_path}")
    print(f"results: {campaign_dir / 'results.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
