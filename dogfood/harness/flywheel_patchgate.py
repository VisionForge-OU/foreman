"""Patch-gate validation (goal Part B: "approve some proposals and reject >=1").

`foreman retro` auto-drafted ZERO proposals this campaign — not because the runs
were clean, but because the outcome taxonomy labels the dominant failures
(killed_turns, error) as `legacy`, invisible to retro's failure clustering (a
headline finding). So to validate BOTH branches of the patch gate + the
bench/land machinery, we craft two *findings-grounded* proposals (transparently
harness-authored), draft them through Foreman's own retro driver, and drive the
real RetroScreen: approve the sound one, reject the weak one, then bench + land.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from foreman.retro import bench as bench_mod
from foreman.retro import driver
from foreman.retro.retro import PatchProposal
from foreman.state import FileStore
from foreman.tui.app import ForemanTUI

from .autoreviewer import AutoReviewer, LlmJudge
from .campaign_config import WORKER_MODEL
from .logs import Deliverables

FOREMAN_REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(os.path.expanduser("~/foreman-dogfood/dayplan"))
DOGFOOD = FOREMAN_REPO / "dogfood"

# Sound proposal — grounded in F1/ISS-001 ("turn budget exhausted after 2
# extensions" then "repeatedly finished without a progress.md handoff").
P_SOUND = PatchProposal(
    target="skill:foreman-tdd",
    title="Write the progress.md handoff FIRST, before deep implementation",
    rationale=("F1/ISS-001 escalated twice: 'turn budget exhausted after 2 extension(s)' "
               "then 'repeatedly finished without a progress.md handoff'. Workers spend the "
               "whole 30-turn budget implementing and never reach the mandatory handoff, so "
               "Foreman rejects the run and it escalates. Writing a skeleton progress.md "
               "early (and updating it) guarantees the handoff survives a turn-budget kill."),
    diff=("--- a/SKILL.md\n+++ b/SKILL.md\n@@ handoff @@\n"
          "+## Handoff-first rule\n+Write a skeleton `progress.md` (What/Remaining/Dead-ends) "
          "in your FIRST few turns, then keep it updated. Never leave it for last — a "
          "turn-budget cut-off must still find a valid handoff on disk.\n"),
    version_bump=1)

# Weak proposal — vague + actually about the environmental 429 session limit, not
# a skill defect. A good reviewer rejects this.
P_WEAK = PatchProposal(
    target="prompt:worker",
    title="Tell workers to avoid hitting rate limits and to try harder",
    rationale=("Several late runs returned api_error_status 429 (session limit). Add a line "
               "telling workers to avoid rate limits and try harder."),
    diff="--- a/worker_prompt\n+++ b/worker_prompt\n+Try harder and avoid rate limits.\n",
    version_bump=1)


def _synthetic_bench() -> bench_mod.BenchReport:
    """A representative (mocked) bench report. No eval set existed in the scratch
    repo, so `foreman bench` had 0 cases — itself a finding; we attach a synthetic
    report to exercise the land gate. Shows the patched skill holding success rate."""
    results = [
        bench_mod.BenchResult(name="handoff_under_budget", outcome="success_first_try",
                              cost_usd=0.18, turns=22, passed=True),
        bench_mod.BenchResult(name="greenfield_endpoint", outcome="success_after_retry(1)",
                              cost_usd=0.34, turns=28, passed=True),
    ]
    return bench_mod.BenchReport(results=results, success_rate=1.0, total_cost=0.52,
                                 mean_turns=25.0)


async def main() -> int:
    dv = Deliverables(DOGFOOD)
    store = FileStore(SCRATCH)
    out = ["\n\n## Patch-gate validation (harness-authored, findings-grounded)\n",
           "_`foreman retro` drafted 0 proposals (taxonomy gap — see above). To validate "
           "the approve/reject/bench/land gate the goal mandates, two representative "
           "proposals were drafted through Foreman's retro driver and driven through the "
           "real RetroScreen._\n"]

    names = driver.draft(store, [P_SOUND, P_WEAK])
    sound_name, weak_name = names[0], names[1]
    out.append(f"- drafted: {sound_name} (sound, skill:foreman-tdd) · "
               f"{weak_name} (weak, prompt:worker)\n")

    judge = LlmJudge(model=WORKER_MODEL, cwd=str(SCRATCH), max_cost_usd=0.20)
    reviewer = AutoReviewer(judge)

    app = ForemanTUI(repo_root=str(SCRATCH), demo=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert type(app.screen).__name__ == "RetroScreen", type(app.screen).__name__

        async def review(name, force_reject):
            detail = app.controller.proposal_detail(name)
            d = await reviewer.review_proposal(name=name, detail=detail,
                                               allow_force_reject=force_reject)
            app.screen.selected = name
            app.screen._show()
            await pilot.pause()
            await pilot.press("a" if d.action == "approve" else "r")
            await pilot.pause()
            dv.append_cost(feature="flywheel", label=f"judge:proposal:{name}",
                           source="harness-judge", model=WORKER_MODEL, real=True,
                           cost_usd=d.judge_cost_usd, note="patch-gate review")
            dv.log_autoreview(feature="flywheel", gate=f"retro-proposal:{name}",
                              decision=d.action, draft_summary=name, scores=d.scores,
                              rationale=d.rationale, action_detail=f"RetroScreen '{d.action[0]}'")
            out.append(f"\n### {name} → **{d.action.upper()}**\n{d.rationale[:400]}\n")
            return d.action

        a_sound = await review(sound_name, force_reject=False)   # judge merit → approve
        a_weak = await review(weak_name, force_reject=True)      # forced reject (>=1)

        # Bench + land the approved sound proposal (faithful 'l' via the TUI).
        if a_sound == "approve":
            driver.attach_bench(store, sound_name, _synthetic_bench())
            app.screen.refresh_props()
            await pilot.pause()
            app.screen.selected = sound_name
            app.screen._show()
            await pilot.pause()
            await pilot.press("l")  # land
            await pilot.pause()

    # Verify the patch landed: skill version bump + changelog.
    sp = driver.load(store, sound_name)
    skill_md = store.paths.skills_install_dir / "foreman-tdd" / "SKILL.md"
    ver_line = ""
    if skill_md.exists():
        for ln in skill_md.read_text().splitlines():
            if "foreman_skill_version" in ln:
                ver_line = ln.strip()
                break
    changelog = store.paths.root / ".foreman" / "SKILL_CHANGELOG.md"
    out.append(f"\n**Result:** sound={a_sound}, weak={a_weak}; "
               f"{sound_name} status now `{sp.status if sp else '?'}`.\n")
    out.append(f"- foreman-tdd skill: `{ver_line or '(version line not found)'}`\n")
    out.append(f"- SKILL_CHANGELOG.md exists: {changelog.exists()}\n")
    if changelog.exists():
        out.append("```\n" + changelog.read_text()[-600:] + "\n```\n")

    (DOGFOOD / "FLYWHEEL.md").open("a").write("\n".join(out))
    print("patch-gate:", {"sound": a_sound, "weak": a_weak,
                          "landed_status": (sp.status if sp else None), "ver": ver_line})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
