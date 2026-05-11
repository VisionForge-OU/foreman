"""C3 — close the learning flywheel on the accumulated real run data.

1. Reconcile one feature's metrics-pane cost against its usage.json (snapshot).
2. `foreman retro` over the runs → gated skill/prompt proposals.
3. Auto-review each proposal through the REAL RetroScreen via Pilot (approve the
   sound ones, reject >=1 weak one) with logged rationale.
4. `foreman bench` (mocked) → attach reports; land approved+benched proposals.
All steps are best-effort and record honestly what happened (incl. "nothing to
propose" or "bench had no eval set") rather than faking a result.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from .autoreviewer import AutoReviewer, LlmJudge
from .campaign_config import WORKER_MODEL
from .guardrails import foreman_spend
from .logs import Deliverables

FOREMAN_REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(os.path.expanduser("~/foreman-dogfood/dayplan"))
DOGFOOD = FOREMAN_REPO / "dogfood"


def _cli(args: list[str], timeout=900) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", "foreman", *args], cwd=str(FOREMAN_REPO),
                          capture_output=True, text=True, timeout=timeout)


def reconcile_metrics(fly: list[str]) -> None:
    """Compare the metrics pane's total_cost with the raw usage.json sum (one feature)."""
    from foreman.state import FileStore
    from foreman.retro import metrics
    store = FileStore(SCRATCH)
    feats = store.list_features()
    if not feats:
        fly.append("## Metrics reconciliation\n\n(no features)\n")
        return
    slug = feats[0]
    m = metrics.load_feature_metrics(store, slug)
    pane = metrics.render(m)
    # raw sum from usage.json
    fdir = Path(store.paths.feature_dir(slug))
    raw = 0.0
    for u in fdir.glob("runs/*/usage.json"):
        try:
            raw += float(json.loads(u.read_text()).get("cost_usd", 0) or 0)
        except Exception:
            pass
    pane_cost = getattr(m, "total_cost", None)
    fly.append("## Metrics-pane reconciliation (" + slug + ")\n")
    fly.append("```\n" + pane + "\n```\n")
    fly.append(f"- metrics-pane total_cost: ${pane_cost}\n")
    fly.append(f"- raw sum of runs/*/usage.json: ${raw:.4f}\n")
    delta = abs((pane_cost or 0) - raw)
    fly.append(f"- reconciliation delta: ${delta:.4f} "
               f"({'MATCH' if delta < 0.01 else 'MISMATCH — finding'})\n")


async def review_proposals(dv: Deliverables, fly: list[str]) -> dict:
    from foreman.tui.app import ForemanTUI
    judge = LlmJudge(model=WORKER_MODEL, cwd=str(SCRATCH), max_cost_usd=0.20)
    reviewer = AutoReviewer(judge)
    verdicts = {}
    app = ForemanTUI(repo_root=str(SCRATCH), demo=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("t")  # RetroScreen
        await pilot.pause()
        if type(app.screen).__name__ != "RetroScreen":
            fly.append("\n## Retro gate\n\nRetroScreen did not open (finding).\n")
            return verdicts
        props = app.controller.retro_proposals()
        fly.append(f"\n## Retro proposals ({len(props)})\n")
        for sp in props:
            detail = app.controller.proposal_detail(sp.name)
            fly.append(f"\n### {sp.name}  ·  target={sp.proposal.target}  ·  status={sp.status}\n")
            fly.append("```\n" + detail[:1600] + "\n```\n")
            decision = await reviewer.review_proposal(
                name=sp.name, detail=detail, allow_force_reject=True)
            dv.append_cost(feature="flywheel", label=f"judge:retro:{sp.name}",
                           source="harness-judge", model=WORKER_MODEL, real=True,
                           cost_usd=decision.judge_cost_usd, note="proposal review")
            # enact via the TUI
            screen = app.screen
            screen.selected = sp.name
            screen._show()
            await pilot.pause()
            await pilot.press("a" if decision.action == "approve" else "r")
            await pilot.pause()
            verdicts[sp.name] = decision.action
            fly.append(f"\n**Auto-review verdict: {decision.action.upper()}** — "
                       f"{decision.rationale[:300]}\n")
            dv.log_autoreview(feature="flywheel", gate=f"retro-proposal:{sp.name}",
                              decision=decision.action, draft_summary=f"target={sp.proposal.target}",
                              scores=decision.scores, rationale=decision.rationale,
                              action_detail=f"RetroScreen '{'a' if decision.action=='approve' else 'r'}'")
    return verdicts


def bench_and_land(verdicts: dict, fly: list[str]) -> None:
    # bench (mocked) — try to attach to each approved proposal and land it.
    fly.append("\n## Bench + land\n")
    approved = [n for n, v in verdicts.items() if v == "approve"]
    if not approved:
        fly.append("No approved proposals to bench/land.\n")
        return
    for name in approved:
        cp = _cli(["bench", str(SCRATCH), "--proposal", name], timeout=600)
        out = (cp.stdout + cp.stderr).strip()
        fly.append(f"\n### bench {name} (rc={cp.returncode})\n```\n{out[-1200:]}\n```\n")
        # attempt to land via CLI driver
        from foreman.state import FileStore
        from foreman.retro import driver
        store = FileStore(SCRATCH)
        try:
            msg = driver.land(store, name)
            fly.append(f"**LANDED {name}:** {msg}\n")
        except Exception as e:
            fly.append(f"**Land blocked for {name}:** {e}\n")


async def main() -> int:
    dv = Deliverables(DOGFOOD)
    fly: list[str] = ["# FLYWHEEL — close the loop (C3)\n",
                      f"_generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}_\n"]
    reconcile_metrics(fly)

    print("[flywheel] running foreman retro …")
    cp = _cli(["retro", str(SCRATCH)], timeout=1200)
    fly.append("\n## `foreman retro` output\n```\n" + (cp.stdout + cp.stderr)[-2000:] + "\n```\n")
    print(f"[flywheel] retro rc={cp.returncode}")

    verdicts = await review_proposals(dv, fly)
    bench_and_land(verdicts, fly)

    fly.append(f"\n---\nTotal real spend at flywheel end: "
               f"${foreman_spend(SCRATCH) + dv.harness_spend():.2f}\n")
    (DOGFOOD / "FLYWHEEL.md").write_text("\n".join(fly))
    print("[flywheel] wrote FLYWHEEL.md; verdicts:", verdicts)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
