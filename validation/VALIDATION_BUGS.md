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

### B5 — Rejecting a PRD amendment was a silent drop, not fix issues (H6) — major — **FIXED**
- **Found during:** CHECKPOINT H6 (TUI integration test pass). The auditor correctly drafts a PRD
  amendment on a divergence and re-enters the hash-sealed review gate (Step 5.2 machinery PASS), and
  `audit.fix_issue_bodies()` exists with a docstring stating it is "used when a human **rejects** an
  amendment". DECISIONS.md §WS5 likewise promised "approve ⇒ re-seal; reject ⇒ new fix issues".
- **Bug:** `fix_issue_bodies()` was **called nowhere** in `src/`. `ReviewScreen.action_request_changes`
  → `controller.request_changes` → `store.request_changes` just flipped the PRD to
  `changes_requested`; nothing turned the divergence into work. The TUI even told the reviewer to
  "re-run grill/planner to revise" — wrong for an amendment. So rejecting the amendment **silently
  dropped** the divergence: the code stayed divergent and no fix issue was ever created, directly
  contradicting the H6 contract ("rejection turns the amendment into fix issues, not a silent drop").
- **Severity:** major (gate/flywheel-integrity gap on the human side; the divergence the auditor
  found could vanish with no trace of remediation).
- **Fix applied (`scheduler.py`, `tui/controller.py`, `tui/app.py`, `audit.py`):**
  - `audit.report_from_raw()` rebuilds an `AuditReport` from the persisted `runs/<id>/audit.json`.
  - `Scheduler.reject_amendment(slug, comments)` reloads the latest audit, **keeps the approved spec**
    (strips the `## PRD Amendment` section and re-seals the PRD), then spins each diverged/unimplemented
    finding into a queued, buildable `FIX-NNN` issue (acceptance check = the configured test command so
    the WS1.1 gate admits it; unknown footprint ⇒ runs alone), seeds Default-FAIL verification, and
    drops the feature back into `BUILDING`.
  - `controller.request_changes` detects a PRD carrying the amendment heading and routes to
    `reject_amendment`, returning the new ids; `ReviewScreen.action_request_changes` reports
    "amendment rejected — created N fix issue(s)" instead of the misleading revise message.
- **Verified:** `test_reject_amendment_spins_off_fix_issues`,
  `test_rejected_amendment_fix_issue_builds_and_merges` (build → reject → rebuild → FIX issue merges),
  `test_request_changes_on_prd_amendment_creates_fix_issues`,
  `test_request_changes_on_ordinary_prd_does_not_create_issues` (plain revise loop unchanged),
  `test_review_screen_reject_amendment_creates_fix_issues` (TUI path + message). Full suite: 292 passed.

### B6 — No TUI path to review a retro proposal; gate was CLI-only (H7) — major — **FIXED**
- **Found during:** CHECKPOINT H7. The landing gate is sound and tested (Step 5.4: `is_landable` +
  `driver.land` refuse anything not approved-and-sealed AND benched), but there was **no controller or
  TUI surface** to list / inspect / approve / reject / land a proposal — only the `foreman retro` /
  `foreman bench` CLI and hand-editing files. The human checkpoint ("open one proposal in review;
  inspect the diff + attached bench delta; reject it / approve to test landing") was undriveable from
  the TUI, so the patch-approval gate could not be exercised by the operator where they work.
- **Severity:** major (the human half of the WS6 flywheel gate had no UI; an operator could only land
  patches by running CLI subcommands by hand).
- **Fix applied (`retro/driver.py`, `tui/controller.py`, `tui/app.py`):**
  - `driver.reject()` (status → `rejected`, can never land — kept for the audit trail, not deleted),
    `driver.bench_report()` / `driver.list_names()` accessors.
  - Controller surface: `retro_proposals`, `retro_proposal`, `proposal_detail` (status + bench delta +
    sealed diff/rationale body), `approve_proposal`, `reject_proposal`, `land_proposal`.
  - New TUI **RetroScreen** (`t` from the dashboard): lists proposals, shows the selected proposal's
    diff + rationale + attached bench delta, and `a`/`r`/`l` approve/reject/land — the landing gate
    (not-approved / no-bench) surfaces as a notify error, nothing lands without approval AND a bench
    report. Generation/benchmarking deliberately stay on the CLI (long, token-spending agent runs).
- **Verified:** `test_controller_proposal_review_and_landing_gate` (gate: blocked pre-approval, blocked
  without bench, lands with both), `test_controller_reject_proposal_blocks_landing`,
  `test_retro_screen_reviews_proposal_and_enforces_gate`, `test_dashboard_opens_retro_screen`.

### B7 — Build report omitted retries (H4) — minor — **FIXED**
- **Found during:** CHECKPOINT H4. The checkpoint asks the final report to show "cost / **retries** /
  escalations"; `BuildReport.render()` had cost and escalations but **no retries** line, even though
  every issue carries an `attempts` count.
- **Fix applied (`scheduler.py`):** `BuildReport.retries` (Σ non-janitor issue attempts), populated in
  `_tally`, rendered as `- Retries: N · Escalations: M`. Verified: `test_report_includes_retries_count`.

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

### B3 — H2 allowed PRD-only advancement without a sealed ADR — major — **FIXED**
- **Found during:** CHECKPOINT H2 audit. The store could approve/seal both `adr.md` and `prd.md`, and
  individual docs correctly blocked approval while open questions remained, but phase derivation,
  `run_slicer`, and `Scheduler.build` only required an approved PRD. A manually approved PRD plus an
  unapproved/tampered ADR could still advance to slicing/building, contradicting the H2 contract to
  approve both docs and have both sealed before downstream work.
- **Impact:** major gate-integrity gap for the grill stage. ADR review could be skipped accidentally
  or invalidated after approval without blocking Phase B.
- **Fix applied (`state.py`, `pipeline.py`, `scheduler.py`):** phase progression, slicer start, and
  build start now require both `adr.md` and `prd.md` to be `approved`. README/DECISIONS updated to
  describe the paired-doc gate.
- **Review-DX hardening:** `foreman-grill-docs` and `SkillInvocation.grill` now require the
  `## Decisions made on your behalf` digest in both ADR and PRD; the digest extractor now surfaces
  the required `_None ...` line instead of hiding it.
- **Verified:** focused H2/TUI regression set `74 passed`; earlier full suite before the final
  ADR+PRD revision-loop regression was `279 passed in 207.17s` (not rerun at the user's request).
  New/updated tests: `test_doc_review_requires_both_adr_and_prd_approved`,
  `test_grill_revision_feeds_comments_for_adr_and_prd`,
  `test_slicer_requires_both_adr_and_prd_approved`,
  `test_build_requires_approved_adr_even_if_queue_confirmed`,
  `test_review_screen_blocks_approval_with_open_questions`,
  `test_decisions_digest_keeps_none_line`.

### B4 — Queue review hid issue frontmatter details needed for H3 — major — **FIXED**
- **Found during:** CHECKPOINT H3 audit. The dashboard entered `queue_review` and showed a compact
  conflict graph in the hint line, but the main queue body still rendered only the generic kanban
  issue-id buckets. The reviewer could not see each issue's `acceptance_check`, `touches`, `prd_refs`,
  or dependencies before pressing confirm.
- **Impact:** major UX/gate-review gap. The data existed on disk and build-time machinery enforced
  missing acceptance checks, but the human queue-review checkpoint could not verify slice quality or
  parallelism inputs from the queue screen itself.
- **Fix applied (`tui/controller.py`, `tui/app.py`):** added `queue_review_text()` and render it during
  `Phase.QUEUE_REVIEW`. It lists every feature issue with `depends_on`, `acceptance_check`, `touches`,
  and `prd_refs`, followed by the conflict graph. The graph now also lists `no overlaps` nodes so
  disjoint slices are visible rather than inferred from missing edges.
- **Verified:** `test_queue_review_shows_checks_touches_refs_graph_and_confirms`; focused H3 set
  `tests/test_tui.py tests/test_controller.py tests/test_conflicts.py tests/test_verification.py -q`
  → `34 passed`.

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
