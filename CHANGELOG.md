# Changelog

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
