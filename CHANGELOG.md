# Changelog

## 0.4.10

### Fixed
- **Plan revise loop ignored the reviewer's comment (found in checkpoint H1).** When you
  requested changes on a plan and re-ran the planner, `run_planner` rebuilt the prompt from
  the *original* feature request only — it never passed the reviewer's comment or the prior
  plan (unlike the grill loop). It appeared to work because the planner happened to read
  `.foreman/reviews/` while exploring the repo, but that's incidental and unreliable. The
  planner now receives the prior plan + the latest `request_changes` comment, is told to
  address every comment and keep earlier requirements, and appends a `## Changelog`.


## 0.4.9

### Changed
- **Vendored skills/agents auto-refresh — no more manual `foreman init` after an
  upgrade.** After upgrading Foreman, a target repo's `.claude/skills/foreman-*` and
  `.claude/agents/foreman-*` go stale (status shows `[outdated]` with a ✗). Builds and
  pipeline phases now bring those Foreman-owned files current in place (idempotent;
  they're git-excluded so they don't touch your history). A genuinely *missing* required
  skill is still a hard error (not silently reinstalled). Outdated never blocked a build —
  it was only a stale status — but you no longer have to re-init each upgrade.


## 0.4.8

### Fixed
- **`foreman-tdd skill is not registered` (worker then went `stuck`).** Issue worktrees
  are forked from the integration branch, which usually does NOT have the vendored
  `foreman-*` skills/agents committed — `foreman init` installs them into the repo's
  working tree, but they're typically left untracked, so a fresh worktree had no
  `.claude/skills/`. The worker couldn't load `foreman-tdd` (and the evaluator couldn't
  run as `foreman-evaluator`), floundered, and got killed for no progress. Foreman now
  provisions the vendored skills + agents into each issue worktree, git-excluded
  (`.claude/skills/foreman-*`, `.claude/agents/foreman-*`) so they never leak into a
  merge. (Earlier cheaper models silently ignored the skill and coded directly; stricter
  models correctly errored.)


## 0.4.7

### Fixed
- **`working directory does not exist … (worktree creation may have failed)` during a
  run.** `_work_issue` created the issue worktree *before* taking the per-issue lock,
  and the worktree path is shared per issue id. When a second worker grabbed the same
  issue (a resume overlapping a build, or two builds), its `create_issue_worktree`
  removed and recreated the **live** worker's worktree, then removed it again on
  backoff — so the first worker's evaluator (or worker) found its cwd gone. The lock is
  now acquired **before** any worktree work, so a second worker backs off cleanly and
  the live worktree is never touched. (Worktree/hook setup failures release the lock
  too, so it can't leak.)


## 0.4.6

### Fixed
- **Run errored with `ValueError: ... chunk is longer than limit`.** Foreman reads the
  `claude` subprocess output line by line, but asyncio's default stream buffer is only
  64 KiB — a single large stream-json event (a big tool result, a file read, a large
  diff — common for the evaluator) overflowed it and killed the run. The reader now
  uses a 64 MiB limit and, as a backstop, skips an over-limit line and keeps reading
  instead of crashing the whole run.


## 0.4.5

### Fixed
- **TUI crash rendering worker logs with brackets** (`MarkupError: Expected markup
  value …`). Worker log lines are raw agent output (shell commands) and routinely
  contain an unbalanced `[` (e.g. a truncated `if [ -f …`). They were passed straight
  to `Static.update()`, which parses content as Textual markup, so an unclosed bracket
  crashed the Workers screen. All agent/file-derived text shown in the TUI (worker log,
  global log, status bar, escalation detail, doc open-questions/digest) is now escaped;
  intentional markup is preserved.


## 0.4.4

### Changed
- **Evaluator grounds its verdict in the current worktree (→ agent v3).** Investigating
  a stuck issue showed the evaluator objecting to "remove duplicate file X" that the
  worker had *already removed* — it over-explored (read 10+ files), ran out of turns,
  and the turn-extension resume emitted the verdict from stale context. (The worker and
  evaluator worktrees were verified identical — same cwd — so this was a grading-accuracy
  issue, not a worktree mismatch.) The evaluator now starts from the diff, reads only the
  files it touches, and must confirm a file's CURRENT state before objecting about it.
  Re-run `foreman init` in a target repo to pick up the improved evaluator agent.


## 0.4.3

### Fixed
- **Endless builder↔evaluator loop: a `pass` verdict with a noted nit was rejected.**
  `Verdict.is_pass` required an *empty* objections list, so when the evaluator returned
  `verdict: "pass"` but listed a minor suggestion, Foreman treated it as a failure and
  bounced the work to a fresh builder — which the evaluator then re-nitpicked, forever.
  `is_pass` now trusts the `verdict` field (objections on a `pass` are advisory) and
  keeps the rubric-score guardrail. The evaluator agent prompt (→ v2) was also
  recalibrated: pass when the acceptance check passes and every dimension ≥ 3/5;
  reserve `objections` for concrete, blocking defects, not stylistic nitpicks.
  Re-run `foreman init` in a target repo to pick up the improved evaluator agent.

## 0.4.2

### Fixed
- **Crash after answering an escalation when you navigate away.** The "resume" runs in
  a background worker; when it finished it called `refresh_escs()`, which queried the
  `#elist` widget. If the resume outlived the Attention screen (long resume + you left
  the screen), the widget was gone → `NoMatches` crashed the app. `refresh_escs` now
  bails if its widgets are gone (`is_mounted` is unreliable here), and the resume result
  is surfaced via the app (which always outlives the screen).

## 0.4.1

### Fixed
- **Attention screen: "Answer & resume" was bound to Enter, conflicting with the
  answer box.** Enter is needed for newlines in the answer TextArea (and selection in
  the escalation list), so submitting was ambiguous and unreliable depending on focus.
  Submit is now **Ctrl+S** (a priority binding that works whether or not the answer box
  is focused); Enter stays a plain newline. "Next escalation" moved from Tab to Ctrl+N
  so Tab can move focus normally. The answer-box label shows the keys.

## 0.4.0

### Changed
- **Turn-budget extensions now cover the evaluator, auditor, and e2e agents.**
  Previously only build workers and the Phase-A agents (planner/grill/slicer) could
  resume on a turn cut-off; the read-only evaluator that runs out of turns mid-grading
  would produce an unparseable verdict and escalate the issue. These agents now resume
  the same session with more turns (up to `max_turn_extensions`) to finish, governed by
  the existing `auto_extend_turns` / `max_turn_extensions` / `turn_extension_size`
  config. Factored into a shared `_run_agent_with_extensions` helper.

## 0.3.2

### Fixed
- **Worker sidebar flicker + crash on selecting a worker.** The Workers screen
  rebuilt its list (clear + re-append) every 0.3s, which flickered, wiped the arrow-key
  highlight, and raced with click handling — clicking a worker crashed with a Textual
  `ValueError` (the clicked item had just been cleared from the node list). The list
  now updates labels in place and only rebuilds when the set of workers changes;
  arrow/tab navigation follows the highlight into the log pane.
- **`RuntimeError: aclose(): asynchronous generator is already running`.** When a run
  was cancelled mid-step (e.g. during the TUI teardown above), the runner closed the
  backend stream while a `__anext__` was still in flight. It now drains the in-flight
  step before closing, so a cancelled run ends cleanly.

## 0.3.1

### Fixed
- **Build failed to start when the repo is checked out on the integration branch**
  (`fatal: 'main' is already used by worktree …`). Git refuses a second worktree on a
  branch the primary checkout already holds — the common case where your repo sits on
  `main`. Foreman now uses the repo itself as the integration worktree in that case, so
  merges land directly on your branch (the intended deliverable). A safety guard also
  prevents the worktree cleanup from ever removing the primary checkout. Test fixtures
  used plain `git init` (default `master`), which masked the bug.

## 0.3.0

### Added
- **Turn-budget awareness + request-more-turns / continue.** Agents and workers are
  now told their per-run turn budget and asked to finish within it. A worker that is
  making progress but running low can emit `request_more_turns: N` in its
  FOREMAN-SUMMARY (instead of being cut off); and a hard turn cut-off is treated as an
  implicit request. In both cases Foreman **resumes the same session** with a fresh
  turn allowance and the agent **continues where it left off**, up to
  `max_turn_extensions` (default 2) before escalating to a human. Applies to build
  workers and the Phase-A agents (planner/grill/slicer) — the planner previously hit
  the turn limit and was thrown away every run. Only turn exhaustion extends; cost /
  timeout / stuck kills still escalate. New config: `auto_extend_turns`,
  `max_turn_extensions`, `turn_extension_size`. (`foreman-tdd` skill → v3.)

## 0.2.0

First release after an end-to-end dogfooding shakedown (see `validation/`). Hardens
the TUI and the Phase-A document pipeline; adds live activity visibility.

### Added
- **Live TUI status line.** The dashboard now shows a persistent, spinner-animated
  status bar — `ACTIVE · planner · turn 4 · 12s · ⚙ Bash(…)` while work runs, or
  `idle · <last event>` otherwise. Phase-A agents (planner/grill/slicer) now stream
  their activity into the global log instead of running silently.

### Fixed
- **Plan/ADR/PRD "reverted to v1" during a run.** Document agents now write to a
  Foreman-owned draft path (`feature/drafts/<kind>.md`); only Foreman writes the
  canonical doc. The version-of-record can no longer be corrupted or read mid-write —
  it stays at the prior version until Foreman re-stamps it.
- **TUI crash on non-canonical doc status** (`ValueError: 'draft' is not a valid
  DocStatus`). Doc loading is now tolerant (unknown status → a non-approved state),
  mirroring issue-status loading; never crashes on a mid-write or hand-edited file.
- **TUI crash selecting list items** on Textual 8 (`Label.renderable` removed). List
  selection now reads the item's `name`, independent of Textual internals.
- **Crash-recovery orphan reconciliation (B1).** After a hard crash (SIGKILL),
  an issue left mid-flight (`in_progress`/`tests_failing`/`awaiting_evaluation`) is now
  requeued on restart instead of silently stalling; no duplicate merge.
- **`foreman init` no longer guesses uninstalled tools** (e.g. `typecheck: mypy .` on a
  project without mypy). Command detection now gates on tool availability.

## 0.1.0
- Initial Phase 1 + Phase 2 implementation: gated plan→ADR/PRD→issues→TDD→e2e
  pipeline, conflict-aware scheduler, verification gate + regression ratchet,
  read-only evaluator/auditor, janitor passes, retro/bench flywheel, TUI.
