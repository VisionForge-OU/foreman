"""Real-run conductor entry point (staged + resumable).

Usage (from the foreman worktree):
  uv run python -m dogfood.harness.run_campaign --setup
  uv run python -m dogfood.harness.run_campaign --baseline
  uv run python -m dogfood.harness.run_campaign --features F5,F1
  uv run python -m dogfood.harness.run_campaign --features F2,F3,F4

Writes all deliverables to dogfood/. Persists dogfood/campaign-state.json after
each feature. Auto-stops at the cost/wall-clock ceilings with a partial report.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import time
from pathlib import Path

from .autoreviewer import AutoReviewer, LlmJudge
from .campaign_config import BACKLOG, WORKER_MODEL, setup_scratch
from .claude_call import run_print_json
from .conductor import Conductor, Timeouts
from .guardrails import Guardrails, foreman_spend
from .logs import Deliverables

FOREMAN_REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(os.path.expanduser("~/foreman-dogfood/dayplan"))
BASELINE_DIR = Path(os.path.expanduser("~/foreman-dogfood/dayplan-baseline"))
DOGFOOD = FOREMAN_REPO / "dogfood"
COST_CEILING = 60.0
WALL_CEILING = 4 * 3600


def _spec(key: str):
    for s in BACKLOG:
        if s.key == key:
            return s
    raise KeyError(key)


async def do_setup(dv: Deliverables) -> dict:
    print(f"[setup] scratch={SCRATCH}")
    res = setup_scratch(SCRATCH, foreman_repo=FOREMAN_REPO, permission_mode="acceptEdits",
                        cost_ceiling=COST_CEILING)
    for n in res["notes"]:
        print("  -", n)
    st = dv.read_state()
    st["setup"] = res
    dv.write_state(st)
    return res


async def do_baseline(dv: Deliverables) -> dict:
    """C1: build F5 the plain way — one ordinary claude -p, no pipeline."""
    f5 = _spec("F5")
    # Fresh copy of the seed (without .foreman) so it matches the pipeline's start.
    if BASELINE_DIR.exists():
        shutil.rmtree(BASELINE_DIR)
    shutil.copytree(SCRATCH, BASELINE_DIR, ignore=shutil.ignore_patterns(
        ".foreman", ".git", ".venv-dayplan", "*.db"))
    # reuse the scratch venv python for the test command
    venv_python = SCRATCH / ".venv-dayplan" / "bin" / "python"
    prompt = (f"You are working in a small FastAPI+SQLite project. TASK: {f5.request}\n"
              f"Edit the code so GET /tasks includes created_at, and make the tests pass. "
              f"Run the tests with: {venv_python} -m pytest -q")
    print("[baseline] running plain claude -p on F5 …")
    t0 = time.monotonic()
    res = await run_print_json(prompt, model=WORKER_MODEL, cwd=str(BASELINE_DIR),
                               permission_mode="acceptEdits", effort="low",
                               max_cost_usd=1.50, timeout_s=600)
    wall = time.monotonic() - t0
    # verify: do tests pass + is created_at returned?
    import subprocess
    tp = subprocess.run([str(venv_python), "-m", "pytest", "-q"], cwd=str(BASELINE_DIR),
                        capture_output=True, text=True)
    created_at_present = subprocess.run(
        ["grep", "-rq", "created_at", str(BASELINE_DIR / "dayplan" / "app.py")]).returncode == 0
    rec = {"wall_s": round(wall, 1), "cost_usd": round(res["cost_usd"], 4),
           "ok": res["ok"], "tests_pass": tp.returncode == 0,
           "created_at_returned": created_at_present, "turns": res["num_turns"],
           "terminal_reason": res["terminal_reason"], "error": res.get("error", "")}
    dv.append_cost(feature="F5-baseline", label="plain-claude", source="baseline",
                   model=WORKER_MODEL, real=True, cost_usd=res["cost_usd"],
                   turns=res["num_turns"], note="C1 plain baseline")
    st = dv.read_state(); st["baseline"] = rec; dv.write_state(st)
    print(f"[baseline] {rec}")
    return rec


async def drive_one_feature(key: str, dv: Deliverables, guard: Guardrails,
                            clock_start: float, max_parallel: int) -> dict:
    from foreman.tui.app import ForemanTUI
    fspec = _spec(key)
    judge = LlmJudge(model=WORKER_MODEL, cwd=str(SCRATCH), max_cost_usd=0.20)
    reviewer = AutoReviewer(judge)
    cond = Conductor(reviewer=reviewer, deliverables=dv, guardrails=guard,
                     snap_dir=DOGFOOD / "snapshots", clock_start=clock_start,
                     timeouts=Timeouts(phase=300, build=2400, gate=20,
                                       rescue_each=1200, review_cycles=2, build_rounds=4),
                     real=True, worker_model=WORKER_MODEL)
    app = ForemanTUI(repo_root=str(SCRATCH), demo=False)
    app.controller.config.limits.max_parallel = max_parallel
    print(f"[{key}] {fspec.ftype}: {fspec.title}  (max_parallel={max_parallel})")
    async with app.run_test() as pilot:
        await pilot.pause()
        debrief = await cond.drive_feature(app, pilot, fspec)
    debrief["findings"] = cond.findings
    debrief["max_parallel"] = max_parallel
    # persist
    st = dv.read_state()
    st.setdefault("features", {})[key] = debrief
    st.setdefault("findings", [])
    st["findings"].extend(cond.findings)
    st["spent_usd"] = round(foreman_spend(SCRATCH) + dv.harness_spend(), 4)
    st["elapsed_s"] = round(time.monotonic() - clock_start, 1)
    dv.write_state(st)
    print(f"[{key}] outcome={debrief['outcome']} "
          f"cost=${debrief.get('cost_usd')} wall={debrief.get('wall_s')}s "
          f"findings={len(cond.findings)} spent=${st['spent_usd']}")
    return debrief


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--features", default="")
    args = ap.parse_args()

    dv = Deliverables(DOGFOOD)
    guard = Guardrails(cost_ceiling_usd=COST_CEILING, wall_clock_seconds=WALL_CEILING,
                       per_run_max_turns=30, per_run_max_cost_usd=1.50)
    st = dv.read_state()
    clock_start = st.get("_clock_start_monotonic") or time.monotonic()
    # (monotonic isn't comparable across processes; for a single long run it's fine.)

    if args.setup:
        await do_setup(dv)
    if args.baseline:
        await do_baseline(dv)

    keys = [k.strip() for k in args.features.split(",") if k.strip()]
    prior_clean = False
    for i, key in enumerate(keys):
        spent = foreman_spend(SCRATCH) + dv.harness_spend()
        status = guard.status(foreman_spend_usd=foreman_spend(SCRATCH),
                              harness_spend_usd=dv.harness_spend(),
                              elapsed_s=time.monotonic() - clock_start)
        if status.should_stop:
            print(f"[STOP] guardrail ceiling hit before {key}: {status.reason} "
                  f"(spent=${status.spent_usd})")
            break
        if not guard.can_afford_run(spent_usd=spent):
            print(f"[STOP] cannot afford another run before {key} (spent=${spent:.2f})")
            break
        # Ramp parallelism only after a prior feature shipped clean.
        mp = 2 if (prior_clean and _spec(key).ftype == "multi") else 1
        debrief = await drive_one_feature(key, dv, guard, clock_start, mp)
        prior_clean = prior_clean or (debrief["outcome"] == "done")

    print("[done] campaign step complete; state at", dv.state_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
