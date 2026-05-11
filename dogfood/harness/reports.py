"""Generate METRICS.md and a machine-aggregated section of ITERATION_REPORT.md
from campaign-state.json + the cost ledger. The narrative parts of the report are
written by hand on top of these aggregates.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

SEV_ORDER = {"blocker": 0, "major": 1, "minor": 2}


def _ledger(dogfood: Path) -> list[dict]:
    p = dogfood / "cost-ledger.json"
    return json.loads(p.read_text()) if p.exists() else []


def gen_metrics(dogfood: Path) -> str:
    state = json.loads((dogfood / "campaign-state.json").read_text())
    ledger = _ledger(dogfood)
    feats = state.get("features", {})
    base = state.get("baseline", {})

    real_cost = sum(e["cost_usd"] for e in ledger if e.get("real"))
    by_source = Counter()
    for e in ledger:
        if e.get("real"):
            by_source[e["source"]] += e["cost_usd"]

    lines = ["# METRICS — scorecard", "",
             f"Total **real** spend: **${real_cost:.2f}** "
             f"(worker ${by_source.get('foreman-worker',0):.2f} · "
             f"judge ${by_source.get('harness-judge',0):.2f} · "
             f"baseline ${by_source.get('baseline',0):.2f})", "",
             "## C1 baseline (plain claude, F5 trivial)"]
    if base:
        lines.append(f"- wall **{base.get('wall_s')}s** · cost **${base.get('cost_usd')}** · "
                     f"turns {base.get('turns')} · tests_pass={base.get('tests_pass')} · "
                     f"first-try success={base.get('ok') and base.get('created_at_returned')}")
    lines += ["", "## Per-feature (pipeline)", "",
              "| key | type | outcome | wall_s | cost_usd | issues | findings |",
              "|-----|------|---------|--------|----------|--------|----------|"]
    for k, d in feats.items():
        lines.append(f"| {k} | {d.get('type')} | {d.get('outcome')} | {d.get('wall_s')} | "
                     f"{d.get('cost_usd')} | {d.get('issue_counts')} | {len(d.get('findings', []))} |")

    # Head-to-head for F5
    f5 = feats.get("F5")
    if f5 and base:
        pcost = f5.get("cost_usd") or 0
        bcost = base.get("cost_usd") or 0
        lines += ["", "## Head-to-head: F5 trivial — pipeline vs plain baseline",
                  f"- plain baseline: ${bcost} / {base.get('wall_s')}s",
                  f"- full pipeline: ${pcost} / {f5.get('wall_s')}s, outcome={f5.get('outcome')}",
                  f"- **pipeline cost multiple: {('%.1fx' % (pcost / bcost)) if bcost else 'n/a'}** "
                  "the plain baseline for the same trivial change."]
    return "\n".join(lines) + "\n"


def gen_findings_table(dogfood: Path) -> str:
    state = json.loads((dogfood / "campaign-state.json").read_text())
    findings = state.get("findings", [])
    # de-dup by (area,title)
    seen, uniq = set(), []
    for f in findings:
        key = (f.get("area"), f.get("title"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    uniq.sort(key=lambda f: SEV_ORDER.get(f.get("severity"), 9))
    lines = ["| severity | area | title | detail |", "|----------|------|-------|--------|"]
    for f in uniq:
        lines.append(f"| {f.get('severity')} | {f.get('area')} | {f.get('title')} | "
                     f"{(f.get('detail') or '')[:160]} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dogfood")
    print(gen_metrics(d))
    print("\n## Auto-detected findings\n")
    print(gen_findings_table(d))
