"""Render an assessment campaign into a self-contained HTML report.

No plotting dependency: tables plus tiny hand-rolled inline-SVG bars. The
report leads with the PASS/FAIL verdict per property (what a reviewer reads
first), then metrics with confidence intervals, paired comparisons between
conditions, and a per-condition breakdown.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .harness import ExperimentResult
from .oracles import Verdict
from .stats import Comparison, MetricStat

_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem auto;max-width:960px;color:#1a1a1a;line-height:1.5}
h1{font-size:1.6rem} h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.92rem}
th,td{text-align:left;padding:.45rem .6rem;border-bottom:1px solid #eee}
th{background:#fafafa} .pass{color:#0a7d34;font-weight:600} .fail{color:#c0392b;font-weight:600}
.badge{display:inline-block;padding:.15rem .6rem;border-radius:4px;font-weight:600;color:#fff}
.ok{background:#0a7d34} .ko{background:#c0392b}
.muted{color:#777;font-size:.85rem} code{background:#f3f3f3;padding:.05rem .3rem;border-radius:3px}
.bar{background:#eee;border-radius:3px;overflow:hidden;height:14px;width:160px;display:inline-block;vertical-align:middle}
.bar>span{display:block;height:100%;background:#3a7bd5}
.ci{color:#555;font-size:.85rem}
.sig{font-weight:600;color:#0a4e8a}
"""


def _bar(fraction: float) -> str:
    pct = max(0.0, min(1.0, fraction)) * 100
    return f'<span class="bar"><span style="width:{pct:.0f}%"></span></span> {fraction:.0%}'


def _fmt_ci(ci: tuple[float, float] | None) -> str:
    if ci is None:
        return ""
    return f' <span class="ci">[{ci[0]:g}, {ci[1]:g}]</span>'


def _verdict_rows(verdicts: list[Verdict]) -> str:
    rows = []
    for v in verdicts:
        cls = "pass" if v.passed else "fail"
        label = "PASS" if v.passed else "FAIL"
        val = "n/a" if v.value is None else f"{v.value:g}"
        rows.append(
            f"<tr><td>{html.escape(v.spec.property)}</td>"
            f"<td>{html.escape(v.spec.name)}</td>"
            f"<td><code>{val}</code>{_fmt_ci(v.ci)} "
            f"(need {html.escape(v.spec.op)} {v.spec.threshold:g})</td>"
            f"<td class='{cls}'>{label}</td></tr>"
        )
    return "\n".join(rows)


def _metric_rows(metric_stats: Mapping[str, MetricStat]) -> str:
    rows = []
    for name in sorted(metric_stats):
        s = metric_stats[name]
        rows.append(
            f"<tr><td><code>{html.escape(name)}</code></td>"
            f"<td>{s.value:g}</td>"
            f"<td class='ci'>[{s.ci_low:g}, {s.ci_high:g}]</td>"
            f"<td>{s.n}</td></tr>"
        )
    return "\n".join(rows)


def _comparison_rows(comparisons: list[Comparison]) -> str:
    if not comparisons:
        return '<tr><td colspan="4" class="muted">no comparisons configured</td></tr>'
    rows = []
    for c in comparisons:
        sig_cls = "sig" if c.p_value < 0.05 else "muted"
        rows.append(
            f"<tr><td>{html.escape(c.name)}</td>"
            f"<td>{c.delta:+g}</td>"
            f"<td class='{sig_cls}'>p = {c.p_value:g}</td>"
            f"<td>{c.n} pairs</td></tr>"
        )
    return "\n".join(rows)


def _condition_rows(results: list[ExperimentResult]) -> str:
    conds = sorted({r.condition for r in results})
    rows = []
    for c in conds:
        sub = [r for r in results if r.condition == c]
        scored = [r for r in sub if r.predicted_category]
        acc = (
            sum(r.predicted_category == r.expected_category for r in scored) / len(scored)
            if scored else 0.0
        )
        errs = sum(r.errors > 0 for r in sub) / len(sub) if sub else 0.0
        rows.append(
            f"<tr><td><code>{html.escape(c)}</code></td><td>{len(sub)}</td>"
            f"<td>{_bar(acc)}</td><td>{_bar(errs)}</td></tr>"
        )
    return "\n".join(rows)


def render_html(
    results: list[ExperimentResult],
    verdicts: list[Verdict],
    out_path: Path,
    *,
    mode: str,
    metric_stats: Mapping[str, MetricStat] | None = None,
    comparisons: list[Comparison] | None = None,
) -> Path:
    overall = all(v.passed for v in verdicts)
    badge = '<span class="badge ok">ALL PROPERTIES PASS</span>' if overall \
        else '<span class="badge ko">SOME PROPERTIES FAIL</span>'

    metrics_section = (
        f"<h2>All metrics (95% CI)</h2>"
        f"<table><tr><th>Metric</th><th>Value</th><th>95% CI</th><th>n tickets</th></tr>"
        f"{_metric_rows(metric_stats)}</table>"
    ) if metric_stats else ""

    comparisons_section = (
        f"<h2>Paired comparisons (permutation test)</h2>"
        f"<table><tr><th>Comparison</th><th>Δ</th><th>p-value</th><th>n</th></tr>"
        f"{_comparison_rows(comparisons or [])}</table>"
    )

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Assessment report</title><style>{_CSS}</style></head><body>
<h1>Agentic Orchestrator — Non-functional assessment</h1>
<p class="muted">Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}
 · mode: <code>{html.escape(mode)}</code> · {len(results)} runs</p>
<p>{badge}</p>

<h2>Property verdicts</h2>
<p class="muted">CI shown next to each measurement; PASS requires the conservative bound to satisfy the threshold.</p>
<table><tr><th>Property</th><th>Check</th><th>Measured</th><th>Verdict</th></tr>
{_verdict_rows(verdicts)}</table>

{comparisons_section}

<h2>Per-condition breakdown</h2>
<table><tr><th>Condition</th><th>Runs</th><th>Category accuracy</th><th>Error rate</th></tr>
{_condition_rows(results)}</table>

{metrics_section}

<p class="muted">Robustness = stability under perturbation + accuracy + clean
termination under fault. Availability = completion, error rate, latency, tokens.
Privacy = PII redaction coverage and leakage into the classifier input.</p>
</body></html>"""

    out_path.write_text(doc, encoding="utf-8")
    return out_path
