"""Sample-size diagnostic for an existing campaign.

Reads a campaign's ``results.json`` and reports, for every metric:

  * the point estimate,
  * the 95% CI width currently achieved,
  * an estimate of how many additional ticket-equivalents would be needed to
    halve that width, using the ``CI_width ∝ 1/sqrt(n)`` asymptotic.

This is *not* a formal power analysis (which would need an effect-size
assumption we don't have yet). It is a practical precision diagnostic:
"are my current campaigns big enough for the comparisons I want to draw?"

Usage:
    python -m assessment.power assessment_runs/results.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from pathlib import Path

from .harness import ExperimentResult
from .metrics import compute_metric_stats


def _load(path: Path) -> list[ExperimentResult]:
    raw = json.loads(path.read_text())
    return [ExperimentResult(**row) for row in raw]


def _additional_tickets_to_halve(n: int) -> int:
    """Asymptotic estimate: CI_width ∝ 1/sqrt(n) ⇒ need 4× n to halve it."""
    return max(0, 4 * n - n)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=Path, help="path to results.json from a prior campaign")
    p.add_argument("--bootstrap-iters", type=int, default=1000)
    p.add_argument("--bootstrap-seed", type=int, default=0)
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    results = _load(args.results)
    stats = compute_metric_stats(
        results, n_bootstrap=args.bootstrap_iters, seed=args.bootstrap_seed,
    )

    print(f"{len(results)} runs · {len({r.ticket_id for r in results})} unique tickets\n")
    print(f"{'metric':<42} {'value':>8} {'CI width':>10} {'×4 budget':>12}")
    print("-" * 78)
    for name in sorted(stats):
        s = stats[name]
        width = s.ci_high - s.ci_low
        budget = _additional_tickets_to_halve(s.n)
        print(f"{name:<42} {s.value:>8.4g} {width:>10.4g} {budget:>12d}")

    print(
        "\nNotes:\n"
        "  · CI is a 95% percentile bootstrap with ticket-level clustering.\n"
        "  · ×4 budget: extra unique-ticket-equivalents needed to roughly halve the CI width\n"
        "    (asymptotic 1/sqrt(n) scaling; ignores ceiling/floor effects near 0 or 1).\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
