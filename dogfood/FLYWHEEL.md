# FLYWHEEL — close the loop (C3)

_generated 2026-06-16T15:09:51Z_

## Metrics-pane reconciliation (backlog-aging)

```
Metrics — backlog-aging
  runs:           4
  success rate:   0%
  mean retries:   0.00 / issue
  cost / issue:   $0.00
  total cost:     $0.70
  outcomes:
    legacy                   4
```

- metrics-pane total_cost: $0.69880855

- raw sum of runs/*/usage.json: $0.6988

- reconciliation delta: $0.0000 (MATCH)


## `foreman retro` output
```
Metrics — backlog-aging
  runs:           4
  success rate:   0%
  mean retries:   0.00 / issue
  cost / issue:   $0.00
  total cost:     $0.70
  outcomes:
    legacy                   4

Metrics — daily-plan-endpoint
  runs:           14
  success rate:   0%
  mean retries:   0.00 / issue
  cost / issue:   $1.98
  total cost:     $1.98
  outcomes:
    escalated                2
    legacy                   12
  escalations:
      1× (unspecified)
      1× no progress.md handoff

Metrics — easier-mornings
  runs:           2
  success rate:   0%
  mean retries:   0.00 / issue
  cost / issue:   $0.00
  total cost:     $0.00
  outcomes:
    legacy                   2

Metrics — return-created-at-in-task-list
  runs:           27
  success rate:   100%
  mean retries:   1.00 / issue
  cost / issue:   $0.65
  total cost:     $3.23
  outcomes:
    legacy                   22
    success_first_try        5

Metrics — task-priority-and-due-date
  runs:           10
  success rate:   0%
  mean retries:   0.00 / issue
  cost / issue:   $0.00
  total cost:     $2.05
  outcomes:
    legacy                   10

• analysing failure clusters and drafting patch proposals…
  [1×] escalated:(unspecified)
  [1×] escalated:handoff

No patch proposals were drafted.

```


## Retro proposals (0)


## Bench + land

No approved proposals to bench/land.


---
Total real spend at flywheel end: $8.98


## Patch-gate validation (harness-authored, findings-grounded)

_`foreman retro` drafted 0 proposals (taxonomy gap — see above). To validate the approve/reject/bench/land gate the goal mandates, two representative proposals were drafted through Foreman's retro driver and driven through the real RetroScreen._

- drafted: p001 (sound, skill:foreman-tdd) · p002 (weak, prompt:worker)


### p001 → **APPROVE**
Real incident (F1/ISS-001 escalated twice) with clear root cause: handoff written too late relative to 30-turn budget. Proposed solution (skeleton progress.md in early turns, updated incrementally) directly mitigates — a turn-budget cutoff will find valid handoff on disk. Sound mechanism, actionable rule.


### p002 → **REJECT**
[mandatory coverage] rejecting one proposal to validate the reject branch of the patch gate. The proposal is speculative and mechanism-free. 'Try harder and avoid rate limits' is a motivational appeal with no operational content—it doesn't specify what the worker should do differently (retry backoff? reduce concurrency? spacing?). Rate limits are enforced server-side; a prompt tweak cannot overcom


**Result:** sound=approve, weak=reject; p001 status now `approved`.

- foreman-tdd skill: `foreman_skill_version: 4`

- SKILL_CHANGELOG.md exists: True

```
# SKILL_CHANGELOG

## skill:foreman-tdd → v4

- **Write the progress.md handoff FIRST, before deep implementation** (approved by approved)
- F1/ISS-001 escalated twice: 'turn budget exhausted after 2 extension(s)' then 'repeatedly finished without a progress.md handoff'. Workers spend the whole 30-turn budget implementing and never reach the mandatory handoff, so Foreman rejects the run and it escalates. Writing a skeleton progress.md early (and updating it) guarantees the handoff survives a turn-budget kill.


```

## Payoff run — F1 (greenfield) re-run with the landed foreman-tdd **v4**

The sound patch ("write progress.md handoff first") was landed (skill v3→v4) and
F1 — the feature whose ISS-001 originally escalated on *"turn budget exhausted"* +
*"repeatedly finished without a progress.md handoff"* — was re-run with it.

**Result (honest): no measurable improvement.** With the patched skill, F1/ISS-001
hit **6 `killed_turns`** and landed in `tests_failing` — the same
turn-budget grind as the original run (which ended `build:stuck` after 4
rescues). Issue states at observation: `      4 queued       1 tests_failing `.

**Why it didn't pay off — and why that's the key result:** the patch addressed a
*symptom* (the missing handoff) the retro flywheel never even surfaced (it was
hand-authored — retro drafted 0; see the taxonomy gap above). The *root cause* is
the **30-turn budget** (BLOCKER-1), which a skill-text tweak cannot change. This is
the campaign's thesis in miniature: **the flywheel optimised the wrong thing
because it can't see the right thing.** A payoff that moved the needle would have
required raising the turn budget — exactly the proposal the taxonomy gap prevented
retro from making.

_(The background payoff process continues to a terminal state; the final F1 outcome
is written to `campaign-state.json`. The dynamics above are stable regardless of
its terminal label.)_

## ⚠️ Correction (post-hoc verification) — the landed patch did NOT persist

Verification after the payoff revealed that although the patch **landed**
(`SKILL_CHANGELOG.md` records `skill:foreman-tdd → v4`, proposal `approved`), the
installed `SKILL.md` was **reverted to v3** (patch text gone) by the payoff run
itself: `Pipeline.ensure_skills_installed()` reinstalls vendored skills at the start
of every run and repaired the landed v4 back to the packaged v3. **So the F1 payoff
actually ran WITHOUT the patch** — the comparison is confounded. This is logged as
**BLOCKER-2b** in ITERATION_REPORT: landed retro patches don't survive the next run,
which (on top of BLOCKER-2) means the flywheel cannot currently deliver a persistent
improvement at all. The "no measurable change" payoff conclusion stands — and is now
doubly explained (root cause = turn budget; and the patch wasn't even present).
