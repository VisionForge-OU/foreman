# ITERATION REPORT — Foreman self-driving campaign

A prioritized, actionable improvement backlog from an **unattended** end-to-end
soak test: a test harness drove the **real Foreman TUI** (Textual Pilot) through
every gate on a real FastAPI+SQLite project, playing the reviewer with a genuine
LLM-judge rubric. 5 features across the work-type spectrum + the Phase-2 flywheel.

### Honesty boundary (read first)
- **Real & actionable:** everything about machinery, agent quality, pipeline
  efficiency, cost, and the TUI gate widgets. These are findings you can act on.
- **Synthetic:** the "reviewer" is an LLM judge, not a human. Findings about
  review **DX, trust, and fatigue are NOT assessed** — deferred to an attended run.
- **Environmental:** the run hit the Claude **subscription session limit (429)**
  near the end, which killed F3's grill and all of F4. Those are *not* pipeline
  failures and are tagged as such.
- One finding is about **my harness**, not Foreman (judge truncated drafts to 6 000
  chars → bounced verbose plans); fixed before the graded run. Flagged where relevant
  so you don't mistake it for a Foreman bug.

Campaign outcomes: **F5 trivial ✅done · F1 greenfield ⚠️stuck · F2 brownfield
❌doc-review · F3 multi ❌grill(turn-kill) · F4 vague ❌429**. Total real spend
~$9–11 of $60. Full numbers in `METRICS.md`; every gate decision in
`AUTOREVIEW_LOG.md`; flywheel in `FLYWHEEL.md`.

---

## Prioritized backlog

### 🔴 BLOCKER-1 — Turn-budget exhaustion is the dominant failure mode
**Evidence:** **21 of 43 agent runs (49%) ended `killed_turns`.** The slicer took
3 runs (2 killed) to emit issues; the feature initializer killed_turns; F1/ISS-001
escalated with *"turn budget exhausted after 2 extension(s)"*; F3's grill
killed_turns then errored out. With `max_turns=30` + haiku + `effort=low`, agents
routinely run out of turns mid-task, then retry/extend/escalate — each killed run
still costs $0.17–0.26.
**Why it matters:** it's the root cause behind F1 (stuck), F3 (grill fail), the
over-cost of F5, and most of the wall-clock. The 30-turn cap (a guardrail this
campaign was told to honor) is simply too tight for a small reasoning model.
**Fix:** make the per-run turn budget **model-aware** (a 30-turn cap suits a
frontier model, not haiku); default higher for cheap models, or auto-scale by
phase (grill/slicer need more than a tdd slice). Make `killed_turns` loud in the
TUI/report (today it's silent — see BLOCKER-2). Consider charging extensions
against a *wall* budget rather than re-running from a turn cap.

### 🔴 BLOCKER-2 — The learning flywheel is blind to the failures that actually happened
**Evidence:** `foreman retro` over the whole campaign drafted **0 proposals**. Not
because runs were clean — because the **outcome taxonomy labelled 38 of 43 runs
`legacy`/blank**, including every one of the 21 `killed_turns` failures. Retro
clusters only `escalated`/`evaluator_bounce`/`human_rejected`, so it saw just two
single-instance escalation clusters and reasonably declined to patch.
**Why it matters:** this is the **central Phase-2 finding**. The flywheel can only
improve what it can measure, and it isn't measuring the dominant failure. A real
operator would conclude "the system is fine, retro found nothing" — which is false.
**Fix:** (a) stamp an outcome on **every** terminal run, including phase agents
(planner/grill/slicer) and the kill reasons (`killed_turns`/`killed_cost`/
`killed_timeout`/`error`); (b) make `retro.cluster_failures` cluster on
`terminal_reason` kills, not just escalations; (c) treat a high `killed_turns`
rate as a first-class proposal trigger (it would have proposed exactly the
turn-budget fix in BLOCKER-1).

### 🔴 BLOCKER-2b — Landed retro patches are silently reverted on the next run
**Evidence:** the patch-gate validation **landed** a foreman-tdd patch (v3→v4):
`.foreman/SKILL_CHANGELOG.md` records `skill:foreman-tdd → v4` and the proposal is
`approved`/sealed. But after the very next pipeline run (the F1 payoff), the
installed `SKILL.md` is **back to v3** and the patch text is gone (`grep -c
Handoff-first` = 0). Mechanism: `Pipeline.ensure_skills_installed()`
(`pipeline.py:84`) and `scheduler.py:204` call `vendored.install()` at the start of
**every** run, which treats the landed (v4) patch as skill *drift* and repairs it
back to the packaged version (v3).
**Why it matters:** this **defeats the entire point of the flywheel.** Even if
retro produced a good proposal (it didn't — BLOCKER-2) and a human approved+benched
it, the improvement evaporates on the next build. It also **confounded this
campaign's payoff run**: the F1 re-run executed with the patch already reverted, so
it never actually tested the patched skill.
**Fix:** make `vendored.install()` version-aware — never downgrade or overwrite an
installed skill whose `foreman_skill_version` is **≥** the packaged one (a landed
retro patch is, by construction, a higher version). Landed patches must outrank the
packaged baseline.

### 🟠 MAJOR-3 — Slicer/planner over-decompose and scope-creep small requests
**Evidence:** F5 = "include `created_at` in the GET /tasks response" (a one-liner)
was sliced into **5 issues**, including **ISS-002 "Update POST /tasks"** (never
requested) and two speculative test issues (ISS-004/005). The planner independently
added the same out-of-scope POST change. The plain baseline did the whole thing
correctly in one 53 s session.
**Why it matters:** over-slicing multiplies the per-issue worker+evaluator+merge
cost (the 5 issues are why F5 cost $3.23 / 31 min) and ships unrequested behaviour.
**Fix:** scale decomposition to request size (a "single trivial slice" path);
instruct the slicer/planner to stay strictly in request scope; have the queue
reviewer flag aggregate over-decomposition (see MAJOR-7 — the synthetic reviewer
missed it too).

### 🟠 MAJOR-4 — Full pipeline is ~29× cost / ~35× time of a plain session for trivial work
**Evidence:** F5 head-to-head — pipeline $3.23 / 1861 s vs baseline $0.11 / 54 s.
**Why it matters:** the pipeline's value is gates + verification for *risky*
change; for trivial change it's pure overhead and it over-builds.
**Fix:** a triage/fast-path that recognizes trivial requests and condenses or skips
gates; or product guidance to route one-liners to a plain session. (Consequence of
BLOCKER-1 + MAJOR-3; fixing those shrinks the gap but won't close it — the gate
ceremony itself is the rest.)

### 🟠 MAJOR-5 — A build can wedge permanently; rescue can't always recover it
**Evidence:** F1/ISS-001 escalated (turn budget, then *"repeatedly finished without
a progress.md handoff"*). The auto-reviewer rescued it **4 times** through the
attention queue; it re-escalated each time and ended `needs_human`, **blocking 3
dependent issues** → feature `build:stuck`.
**Why it matters:** unattended, this burns rescue cycles + tokens with no progress.
**Fix:** detect *repeated identical escalation reason* and stop re-queuing the same
rescue; escalate to "split this issue / raise its budget" instead. Root cause ties
to BLOCKER-1 (the worker never reaches the handoff before the turn cap).

### 🟠 MAJOR-6 — No detection/backoff for provider rate limits (unattended-safety gap)
**Evidence:** at ~23:09 the runs began returning `api_error_status: 429` /
*"You've hit your session limit · resets 3:40am"* at **$0 cost**; Foreman treated
them as ordinary run errors and **burned F3's grill and the entire F4 feature** as
"failed".
**Why it matters:** for an unattended run, a transient quota wall should pause and
resume, not consume features. It also corrupts the metrics (F4 looks like a grill
failure).
**Fix:** detect `api_error_status == 429` / session-limit in the stream result,
**pause-and-retry with backoff** (or park the feature) rather than counting it as a
failed attempt; surface a distinct `blocked:rate_limit` state. (Phase-3 sandbox/
chaos would harden this further — noted as a Phase-3 dependency.)

### 🟡 MINOR-7 — Synthetic-reviewer calibration gaps (real, but the reviewer is synthetic)
- The queue reviewer **approved the over-sliced F5 queue** (5 issues + scope creep)
  — it judges each slice's coherence but not aggregate over-decomposition. A queue
  rubric should include "is this the right *number* of slices for the request?"
- The plan reviewer, at full strictness, bounced a *sound* trivial plan 3× on
  style nits (recalibrated to "approve sound, request-changes only for substantive
  defects"); the demanding-but-fair setting then approved correctly. Calibration is
  load-bearing and worth exposing as a tunable.
- F2 failed `doc_review` because the mandated request-changes + a demanding judge
  never converged in 2 cycles (partly *my* mandated injection — flagged). Underlying
  real signal: the revise→review loop can diverge with a cheap grill model.

### 🟡 MINOR-8 — TUI worker-status never finalizes
**Evidence (Pilot-surfaced):** after a feature completes, `controller.workers[...]`
entries (e.g. `e2e`) stay `status="running"` forever (caught directly: a no-TUI
build returned `phase=done` while `('e2e','running')` lingered). The WorkerScreen
would show ghost "running" workers.
**Fix:** finalize every WorkerLog on phase/run completion (set terminal status).

### 🟡 MINOR-9 — Planner agent is slow (~100 s/run) → gate latency dominates small features
**Evidence:** planner runs logged at 73–113 s each on haiku/`effort=low`; with a
re-plan cycle that's ~3–4 min before grill even starts.
**Fix:** mostly downstream of model/effort choice; consider a lighter planning
prompt for small features.

### 🟡 MINOR-10 — Cosmetic: skill changelog renders reviewer as "approved"
**Evidence:** landed changelog reads *"(approved by approved)"* — the reviewer name
field is mis-interpolated. Trivial.

---

## TUI / gate findings (surfaced by the Pilot driver) — these are real
- ✅ **Every gate is fully driveable** via Pilot keys + widget state — plan/ADR/PRD
  review (`a`/`r`/`#comments`), queue confirm (`c`), attention rescue
  (`#answer`+`Ctrl+S`), retro approve/reject/land (`a`/`r`/`l`). **No crashes**
  across the whole campaign + mock dry-run.
- ✅ **No state-vs-display mismatches detected** — the dashboard hint always
  reflected the disk-truth phase (an explicit check ran at every transition).
- ✅ **File-state-authoritative + crash-safe** held up under fire: when a background
  run was killed mid-feature, disk state was intact and the harness resumed cleanly
  from it. This is a genuine architectural strength.
- ⚠️ MINOR-8 (ghost worker status) is the only TUI-state defect found.
- Snapshots (JSON + SVG) for each gate are in `dogfood/snapshots/`.

## Auto-reviewer (synthetic human) — was it reasonable?
16 approvals / 8 request-changes / 4 escalation answers / 1 reject, all logged with
rationale. The judge showed real judgment (e.g. caught a PRD that dropped the
"ordered for the day" requirement; flagged F5 plan scope-creep). Two calibration
levers matter and should be operator-tunable: **strictness** (demanding vs fair) and
**aggregate checks** (over-slicing). See `AUTOREVIEW_LOG.md`.

---

## "Worth it?" signal — caveated, machinery + agent-success only
- For **trivial work: no** — ~29× cost / ~35× time and it over-builds (MAJOR-3/4).
  Route one-liners to a plain session.
- For **non-trivial work: unproven this run** — every substantial feature (F1/F2/F3)
  failed or stuck, but **the failures were dominated by an over-tight turn budget
  (BLOCKER-1) and a quota wall (MAJOR-6), not by the gate concept.** With a
  model-appropriate turn budget the pipeline's gate/verification value could show;
  this run couldn't demonstrate it.
- The **flywheel did not pay off**, for two compounding reasons that are themselves
  the most valuable results: it **can't see** the dominant failures (BLOCKER-2) and,
  even when a patch is landed, it **doesn't persist** (BLOCKER-2b — reverted on the
  next run). The payoff F1 re-run was confounded by 2b (it ran with the patch already
  reverted) and showed no improvement; independently, the root cause is the turn
  budget, which no skill-text patch can fix.

## What could NOT be assessed without a human (deferred to an attended run)
- Whether the gates **build trust** or feel like **theatre/fatigue**.
- Whether the "decisions made on your behalf" digest + open-questions actually help a
  human review faster (review DX).
- Whether grilling a **vague** request (F4) produces a buildable spec — F4 never ran
  (429). Re-run after quota reset.
- Whether a human reviewer would catch the over-slicing the synthetic one missed
  (MINOR-7).
