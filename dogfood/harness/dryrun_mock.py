"""Free, full-pipeline dry run against the MockBackend (demo=True).

Drives one feature through every gate via the real TUI/Pilot — exercising the
open-question→request-changes→re-grill→approve cycle, the fail-first→retry path,
the evaluator/audit/e2e stages, the metrics pane, snapshots, and all the
deliverable writers — without spending a token. This is the integration test that
proves the conductor before any real spend.
"""
from __future__ import annotations

import asyncio
import time

from foreman.tui.app import ForemanTUI

from .autoreviewer import AutoReviewer, StubJudge
from .conductor import Conductor, FeatureSpec, Timeouts
from .logs import Deliverables


async def main() -> int:
    dv = Deliverables("dogfood/_mock_dryrun")
    reviewer = AutoReviewer(StubJudge())
    guard_start = time.monotonic()
    from .guardrails import Guardrails
    cond = Conductor(reviewer=reviewer, deliverables=dv,
                     guardrails=Guardrails(cost_ceiling_usd=999, wall_clock_seconds=3600),
                     snap_dir="dogfood/_mock_dryrun/snapshots", clock_start=guard_start,
                     timeouts=Timeouts(phase=60, build=240, gate=10, rescue_each=120),
                     real=False, worker_model="mock")

    fspec = FeatureSpec(
        key="mock1", ftype="demo", title="Add done command",
        request="Let users mark a todo item complete via `todo done <id>`.",
        force_rc_gate="")  # the demo PRD already carries a natural open question

    app = ForemanTUI(repo_root=".", demo=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        debrief = await cond.drive_feature(app, pilot, fspec)
        # Bonus: prove we can open the retro + metrics screens without crashing.
        await cond._to_dashboard(pilot, app)
        await pilot.press("t"); await pilot.pause()
        cond._snap(app, "retro-screen")
        if type(app.screen).__name__ != "RetroScreen":
            cond.finding(severity="major", area="tui", title="RetroScreen did not open on 't'")
        await pilot.press("escape"); await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        cond._snap(app, "metrics-screen")
        await pilot.press("escape"); await pilot.pause()

    print("\n==== MOCK DRY-RUN DEBRIEF ====")
    for k in ("key", "type", "outcome", "stages", "issue_counts", "wall_s", "cost_usd"):
        print(f"  {k}: {debrief.get(k)}")
    print("\n  metrics pane:")
    for line in str(debrief.get("metrics_pane", "")).splitlines():
        print(f"    {line}")
    print(f"\n==== FINDINGS ({len(cond.findings)}) ====")
    for f in cond.findings:
        print(f"  [{f['severity']}] {f['area']}: {f['title']} — {f['detail'][:160]}")
    dv.write_state({"phase": "mock_dryrun", "debrief": debrief, "findings": cond.findings})
    ok = debrief.get("outcome") == "done"
    print(f"\nRESULT: {'PASS' if ok else 'CHECK'} (outcome={debrief.get('outcome')})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
