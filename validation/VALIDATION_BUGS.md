# Foreman Validation — Bugs & Divergences

Severity legend: **blocker** / **major** / **minor** / **dx** (UX friction).
Each entry: repro, expected, actual, severity, fix-forward (if any).

---

## Divergences from the validation prompt's assumptions (not necessarily bugs)

### D1 — Hooks / `foreman-test` assets are NOT present after `foreman init` — minor
- **Repro:** `foreman init .` on notesapi, then `find .claude .foreman -iname '*hook*' -o -iname '*foreman-test*'` → nothing.
- **Prompt expectation (Step 2):** "hooks/`foreman-test` assets present" after init.
- **Actual:** By design, the PreToolUse deny hook, Stop hook, and `foreman-test` wrapper are
  installed per-worktree *at build time* into a sibling dir `<worktree>.foreman-hooks/`, not at init.
  This is an intentional design choice (keeps the target repo's `.claude/` clean, avoids diff pollution),
  but it diverges from the prompt's literal expectation. Verified location via code map (hooks/installer.py).
- **Severity:** minor (documentation/expectation mismatch, not a defect).

### D2 — `foreman init` auto-detects `typecheck: mypy .` even when mypy is not a dependency — minor
- **Repro:** init on a FastAPI project with no mypy installed → config gets `typecheck: "mypy ."`.
- **Impact:** Foreman's own verify step would invoke `mypy .` and fail (command-not-found / type errors)
  on a project that never opted into type-checking, potentially blocking every merge.
- **Fix-forward applied:** Set `typecheck: null` and `e2e: null` in notesapi config (no mypy/playwright present).
- **Severity:** minor (sensible-default overreach; easily overridden).

### D3 — `foreman run` halts with exit code 0 on a missing-skill refusal — minor/dx
- **Repro:** delete a required skill, `foreman run ... ` → prints `halted: required skills missing: foreman-tdd` but `echo $?` == 0.
- **Expected:** a halted pipeline start should exit non-zero so CI/automation can detect the refusal.
- **Severity:** minor (dx) — gate works and is visible; only the exit code is wrong for scripting.

---

## Bugs

### B1 — Crash recovery does not reconcile an orphaned `in_progress` issue (F11) — major
- **Repro:** `~/foreman-validation/harness/run_f11.py` — prepare a 2-issue feature (ISS-002 depends on
  ISS-001), build with ISS-002's worker blocked; once ISS-001 is fully **merged** on disk, `SIGKILL -9`
  the whole process group (worker ISS-002 in flight). Restart with a fresh process: `foreman build` equivalent.
- **Expected (prompt F11):** state recovers from disk; no duplicated merges; **worker/worktree state reconciled**.
- **Actual:**
  - ✅ State-of-record recovered: ISS-001 stays `merged`, `verification.json` intact.
  - ✅ **No duplicate merge / no rebuild**: ISS-001 build run-dirs identical before/after restart (`['s0003-ISS-001']`) — the dangerous failure mode does NOT occur.
  - ❌ The interrupted **ISS-002 is left `in_progress`** and is **never requeued**. The restart’s build loop only
    considers `QUEUED` issues (`FeatureState.ready_issues()`, models.py:283); there is no startup reconciliation of
    orphaned `IN_PROGRESS` issues. The only requeue path (scheduler.py:366) handles a *graceful in-process* user-kill,
    not a hard crash. Result: `foreman build` returns immediately having done nothing; the feature silently stalls.
- **Severity:** **major** (recovery is *incomplete* — a human must manually reset the issue’s status to resume).
  NOT a gate-integrity / data-corruption / duplicate-merge blocker: the safety-critical recovery properties hold.
- **Suggested fix:** on `build()` start, reconcile any `IN_PROGRESS` issue with no live lock back to `QUEUED`
  (and clean its worktree), mirroring the §7 user-kill rollback. The stale-lock TTL (900s) also prevents
  *immediate* restart from reclaiming a dead worker’s fresh-heartbeat lock — a related second-order limitation.
- **Fix-forward applied:** none (out of scope for this validation; logged for the team).

## Methodology notes (harness artifacts, NOT Foreman bugs)
- **F5 first run** showed `regressed=[]` because the harness wrote the passing and broken `test_a.py`
  fixtures with **identical byte size in the same second**, so CPython served a stale `.pyc`. Run directly,
  the ratchet correctly reports `failed={test_alpha}`. Fixed by differing fixture size + `-p no:cacheprovider`.
  No Foreman defect — real worktrees never collide this way.
- **F7 first run** merged on attempt 2 because `make_tdd_script(fail_first=True)` is *fail-once-then-pass*
  (passes when it detects a retry). Replaced with a genuinely always-broken worker to exercise retry-exhaustion.

---

## DX findings (from human checkpoints, tagged `[dx]`)

_(awaiting checkpoint feedback)_
