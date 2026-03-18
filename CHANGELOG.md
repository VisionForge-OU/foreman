# Changelog

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
