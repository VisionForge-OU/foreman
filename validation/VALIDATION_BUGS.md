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

### D2 — `foreman init` auto-detects `typecheck: mypy .` even when mypy is not a dependency — minor — **FIXED**
- **Repro:** init on a FastAPI project with no mypy installed → config got `typecheck: "mypy ."`.
- **Impact:** Foreman's own verify step would invoke `mypy .` and fail (command-not-found / type errors)
  on a project that never opted into type-checking, potentially blocking every merge.
- **Fix applied (`installer.py`):** `_detect_commands` now gates every guessed command on `_tool_available`
  (`shutil.which(exe)`, plus for `npm|yarn|pnpm run <script>` that the script is declared in `package.json`).
  An uninstalled tool is left blank instead of guessed. Regression tests added (`test_installer.py`).
- **Caveat:** in environments with universal shims (e.g. pyenv creates `~/.pyenv/shims/mypy` even when
  mypy isn't installed in the active version), `which()` still resolves, so the guess survives. Acceptable —
  the user edits config regardless; the fix is a strict improvement everywhere else.
- **Also:** notesapi config still pins `typecheck: null`, `e2e: null` (cost control for this exercise).
- **Severity:** minor (sensible-default overreach).

### ~~D3 — `foreman run` halts with exit code 0 on a missing-skill refusal~~ — **RETRACTED (false finding)**
- Original claim was a **measurement error**: the Step 2 probe read `$?` *after a pipe to `tail`*, capturing
  tail's exit, not foreman's. Re-measured directly: `foreman run` on a missing-skill halt **exits 2**
  (`_cmd_run` raises `HeadlessError` → `return 2`, propagated via `sys.exit(main())`). No bug. No code change.

---

## Bugs

### B2 — Plan revise loop never feeds the reviewer's comment to the planner (H1) — major — **FIXED**
- **Found during:** CHECKPOINT H1 (live TUI), on `add-tagging-to-notes`. Reviewer requested
  "add color field" on plan v1 and "add a status field (active/archive)" on v3; the revised plans
  did include them (v3: 94 "color" mentions; v4: 48 status mentions), so it *looked* correct.
- **Bug:** `run_planner` builds the revision prompt from `state.request` ONLY — it does not pass the
  reviewer's comment or the prior plan (unlike `run_grill`, which takes `review_comments` + `prev_bodies`).
  `request_changes` records the review but never updates the request. Transcripts confirm the planner only
  consumed the comments **by accident** — it incidentally read `.foreman/features/*/reviews/plan-v*-review.md`
  and `plan.md` while "exploring the repository" (run `478122` hit `reviews/` 7×). A planner that didn't
  explore `.foreman/` would silently ignore the comment and re-emit a plan from the original request.
- **Secondary:** the planner rewrites the plan wholesale each revision rather than accumulating — "color"
  dropped from 94 (v3) to 1 (v4) once the v3 comment shifted focus to "status". No `## Changelog` was ever
  appended to a plan (the grill loop appends one for ADR/PRD; the planner did not).
- **Severity:** major (the plan revise loop's core promise — "the revision consumes your comment" — was
  unreliable, working only via incidental file discovery).
- **Fix applied (`pipeline.py` + `skill_invocation.py`):** `run_planner` now feeds the prior plan body +
  the latest `request_changes` comment into `SkillInvocation.planner(prev_body=, review_comments=)`, which
  instructs the planner to address ALL comments, keep prior requirements, and append a `## Changelog`.
  Regression test `test_planner_revision_feeds_reviewer_comment_and_prior_plan`.

### B1 — Crash recovery does not reconcile an orphaned `in_progress` issue (F11) — major — **FIXED**
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
- **Fix applied (`scheduler.py`):** `build()` now calls `_reconcile_orphans(slug, integ)` right after the
  stale-lock reclaim. Since no worker is running in the fresh process yet, any issue resting in a mid-flight
  status (`IN_PROGRESS`, `TESTS_FAILING`, `AWAITING_EVALUATION`) is a crash orphan: it is reset to `QUEUED`
  (attempt count preserved, so the retry ceiling still applies) and its dead worker's lock is released. The
  re-dispatch forks a fresh worktree (`worktree.create_issue_worktree` cleans up), so no partial state leaks.
- **Verified:** `run_f11.py` now reports `RECONCILE interrupted worker: PASS` — after SIGKILL, ISS-002 is
  requeued and finishes (`merged`), ISS-001 stays `merged` with **no rebuild / no duplicate merge**. New
  regression test `test_orphaned_in_progress_issue_recovered_after_restart` (full suite: 237 passed).
- **Note:** this also resolves the related stale-lock concern — the orphan's fresh-heartbeat lock is force-released
  on reconcile rather than waiting out the 900s TTL.

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
