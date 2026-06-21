# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-05-01

### Added — WS7: skills-suite expansion (4 worker skills + 2 read-only gate agents)
Vendored six new capabilities, sourced from obra/superpowers (MIT) and Anthropic's
skills, all rewritten headless into the `foreman-*` namespace (see NOTICE). The full
suite is green (368 tests).
- **`foreman-debug`** (← superpowers systematic-debugging) — root-cause-first debugging
  the worker leads with on a retry; injected into the worker prompt whenever a distilled
  failure report is present (`context/assembler.py`).
- **`foreman-verify`** (← superpowers verification-before-completion) — self-verification
  the worker runs before claiming done; referenced in every worker prompt's completion
  contract.
- **`foreman-plan`** (← superpowers writing-plans) — the planning stage now follows it
  (`skill_invocation.planner`).
- **`foreman-web-testing`** (← Anthropic web-app-testing + the tdd e2e half) — the e2e
  stage now uses it instead of `foreman-tdd` (`skill_invocation.e2e`).
- **`foreman-code-review`** + **`foreman-security-review`** — two read-only `--agent`
  gate graders that review the committed slice after the evaluator passes. Each emits a
  machine-readable verdict (`foreman-codereview/v1` / `foreman-security/v1`); a blocking
  verdict bounces a fresh builder with the findings, repeated objections or the retry
  ceiling escalate to a human. Both **opt-in** (`code_review_enabled` /
  `security_review_enabled`, default off), wired through `merge_gate.decide()` →
  `scheduler._review` / `_security`.
- `merge_gate.decide()` gained default-disabled `code_review` / `security_review` grader
  stages that reuse the existing bounce/escalate policy; config gained their
  enable/model/budget knobs; `foreman init` installs all six new files.

## [0.5.0] - 2026-04-25

### Changed (internal architecture — no behaviour change)
Eight behaviour-preserving deepenings from an architecture review; the full suite
(335 tests) is green and a fresh adversarial diff review found no behavioural deltas.
- **`seal.py`** — the approval hash invariant (sha256 of body, auto-invalidate on
  edit) lives in one module; gated docs and retro proposals both call it.
- **`prompts.py`** — single owner for the worker/agent/pipeline turn-extension
  continuation text and the distilled failure-report appendix; dead `tdd()` removed.
- **`runner.should_extend()`** — one extend-vs-escalate predicate shared by the
  worker loop, the non-worker agent loop, and the Phase-A pipeline.
- **`verification/merge_gate.py`** — `decide()` collapses the structural gate, the
  evaluator stage, and the bounce/escalate policy into one `GateDecision`.
- **`issue_run.py`** — the per-issue build lifecycle is a deep module; `scheduler.py`
  shrank from 1110 to 782 lines and is now a dispatcher.
- **`FileStore`** owns the `.foreman/` run-artifact + escalation I/O (verdict, audit,
  escalation, report, usage, review snapshots) — orchestrators stop doing file I/O.
- **TUI controller** is a facade (`kill_worker` / `escalation_text` / `config_path` /
  `review_digest`); screens no longer reach into the store/scheduler/review module.
- **`StreamEvent`** exposes `is_assistant` / `is_result` / `made_progress`; CLI
  tool-name knowledge lives behind the parser, so the TUI survives schema drift.

## [0.4.13] - 2026-04-18

### Added
- Validate human checkpoints H4–H7 via TUI/controller integration tests; wire
  amendment-reject path and retro review screen (see 0.4.12 bug fixes B5/B6/B7).

## [0.4.12] - 2026-04-18

### Fixed
- **Worker could bypass the verification.json/issue-file deny hook via MCP tools.** The
  PreToolUse deny hook (WS1.3 trust boundary) only matched native `Write|Edit|MultiEdit|
  NotebookEdit|Bash`, so a worker that follows the user's environment and edits via an
  MCP tool (e.g. lean-ctx `ctx_edit`) or runs shells via `ctx_shell` was never seen by
  the hook — it could write Foreman-owned files. The hook matcher now also covers
  `mcp__*`, and `deny_protected.py` matches by tool-input *shape* (a path + write
  markers, or a command) rather than by native tool name, so MCP edits/shells to
  protected paths are denied while reads and unprotected writes are allowed. Verified
  end-to-end: a real worker's `ctx_edit` of `verification.json` is blocked.

## [0.4.11] - 2026-04-18

### Fixed
- **False `stuck: no file/test progress` when the worker uses MCP tools.** The stuck
  detector only counted native `{Edit, Write, MultiEdit, NotebookEdit, Bash, Skill}` as
  progress. A worker that follows the user's environment and edits/runs via MCP
  equivalents (e.g. lean-ctx `ctx_edit` / `ctx_shell` / `ctx_read`) was seen as idle and
  killed after `stuck_turns` turns despite actively working. Any `mcp__*` tool call now
  counts as progress; pure rumination (no tool calls) is still caught.

## [0.4.10] - 2026-04-11

### Fixed
- **Plan revise loop ignored the reviewer's comment (found in checkpoint H1).** When you
  requested changes on a plan and re-ran the planner, `run_planner` rebuilt the prompt from
  the *original* feature request only — it never passed the reviewer's comment or the prior
  plan (unlike the grill loop). The planner now receives the prior plan + the latest
  `request_changes` comment, is told to address every comment and keep earlier requirements,
  and appends a `## Changelog`.

## [0.4.9] - 2026-04-11

### Changed
- **Vendored skills/agents auto-refresh — no more manual `foreman init` after an
  upgrade.** After upgrading Foreman, a target repo's `.claude/skills/foreman-*` and
  `.claude/agents/foreman-*` go stale (status shows `[outdated]` with a ✗). Builds and
  pipeline phases now bring those Foreman-owned files current in place (idempotent;
  they're git-excluded so they don't touch your history). A genuinely *missing* required
  skill is still a hard error (not silently reinstalled).

## [0.4.8] - 2026-04-11

### Fixed
- **`foreman-tdd skill is not registered` (worker then went `stuck`).** Issue worktrees
  are forked from the integration branch, which usually does NOT have the vendored
  `foreman-*` skills/agents committed — `foreman init` installs them into the repo's
  working tree, but they're typically left untracked, so a fresh worktree had no
  `.claude/skills/`. Foreman now provisions the vendored skills + agents into each issue
  worktree, git-excluded so they never leak into a merge.

## [0.4.7] - 2026-04-11

### Fixed
- **`working directory does not exist … (worktree creation may have failed)` during a
  run.** `_work_issue` created the issue worktree *before* taking the per-issue lock.
  When a second worker grabbed the same issue, it removed and recreated the live
  worker's worktree — so the first worker's evaluator found its cwd gone. The lock is
  now acquired **before** any worktree work.

## [0.4.6] - 2026-04-04

### Fixed
- **Run errored with `ValueError: ... chunk is longer than limit`.** asyncio's default
  stream buffer is 64 KiB; a single large stream-json event overflowed it and killed the
  run. The reader now uses a 64 MiB limit and skips an over-limit line instead of
  crashing.

## [0.4.5] - 2026-04-04

### Fixed
- **TUI crash rendering worker logs with brackets** (`MarkupError: Expected markup
  value …`). Worker log lines are raw agent output and routinely contain an unbalanced
  `[`. All agent/file-derived text shown in the TUI is now escaped; intentional markup
  is preserved.

## [0.4.4] - 2026-04-04

### Changed
- **Evaluator grounds its verdict in the current worktree (→ agent v3).** The evaluator
  now starts from the diff, reads only the files it touches, and must confirm a file's
  current state before objecting about it. Re-run `foreman init` to pick up the improved
  evaluator agent.

## [0.4.3] - 2026-04-04

### Fixed
- **Endless builder↔evaluator loop: a `pass` verdict with a noted nit was rejected.**
  `Verdict.is_pass` now trusts the `verdict` field (advisory objections on a `pass` no
  longer block). Evaluator agent prompt recalibrated to v2: pass when acceptance check
  passes and every dimension ≥ 3/5; `objections` reserved for blocking defects only.

## [0.4.2] - 2026-04-04

### Fixed
- **Crash after answering an escalation when you navigate away.** `refresh_escs` now
  bails if its widgets are gone; resume result is surfaced via the app instead.

## [0.4.1] - 2026-04-04

### Fixed
- **Attention screen: "Answer & resume" was bound to Enter, conflicting with the answer
  box.** Submit is now **Ctrl+S**; Enter stays a plain newline; "Next escalation" moved
  to Ctrl+N.

## [0.4.0] - 2026-03-28

### Changed
- **Turn-budget extensions now cover the evaluator, auditor, and e2e agents.** These
  agents now resume the same session with more turns (up to `max_turn_extensions`) to
  finish, governed by `auto_extend_turns` / `max_turn_extensions` / `turn_extension_size`
  config. Factored into a shared `_run_agent_with_extensions` helper.

## [0.3.2] - 2026-03-28

### Fixed
- **Worker sidebar flicker + crash on selecting a worker.** List now updates labels in
  place and only rebuilds when the set of workers changes.
- **`RuntimeError: aclose(): asynchronous generator is already running`.** Runner now
  drains the in-flight step before closing on cancellation.

## [0.3.1] - 2026-03-28

### Fixed
- **Build failed to start when the repo is checked out on the integration branch**
  (`fatal: 'main' is already used by worktree …`). Foreman now uses the repo itself as
  the integration worktree in that case.

## [0.3.0] - 2026-03-28

### Added
- **Turn-budget awareness + request-more-turns / continue.** Workers and Phase-A agents
  can emit `request_more_turns: N`; Foreman resumes the same session with a fresh
  allowance up to `max_turn_extensions` (default 2). New config: `auto_extend_turns`,
  `max_turn_extensions`, `turn_extension_size`. (`foreman-tdd` skill → v3.)

## [0.2.0] - 2026-03-18

First release after an end-to-end dogfooding shakedown. Hardens the TUI and Phase-A
document pipeline; adds live activity visibility.

### Added
- **Live TUI status line** — `ACTIVE · planner · turn 4 · 12s · ⚙ Bash(…)` while
  work runs, `idle · <last event>` otherwise.

### Fixed
- **Plan/ADR/PRD "reverted to v1" during a run.** Document agents now write to a draft
  path; only Foreman writes the canonical doc.
- **TUI crash on non-canonical doc status** (`ValueError: 'draft' is not a valid DocStatus`).
- **TUI crash selecting list items** on Textual 8 (`Label.renderable` removed).
- **Crash-recovery orphan reconciliation** — issues left mid-flight after a hard crash
  are requeued on restart.
- **`foreman init` no longer guesses uninstalled tools.**

## [0.1.0] - 2026-03-14

### Added
- Initial Phase 1 + Phase 2 implementation: gated `plan → ADR/PRD → issues → TDD → e2e`
  pipeline, conflict-aware scheduler, verification gate + regression ratchet,
  read-only evaluator/auditor, janitor passes, retro/bench flywheel, TUI.

[Unreleased]: https://github.com/VisionForge-OU/foreman/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/VisionForge-OU/foreman/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/VisionForge-OU/foreman/compare/v0.4.13...v0.5.0
[0.4.13]: https://github.com/VisionForge-OU/foreman/compare/v0.4.12...v0.4.13
[0.4.12]: https://github.com/VisionForge-OU/foreman/compare/v0.4.11...v0.4.12
[0.4.11]: https://github.com/VisionForge-OU/foreman/compare/v0.4.10...v0.4.11
[0.4.10]: https://github.com/VisionForge-OU/foreman/compare/v0.4.9...v0.4.10
[0.4.9]: https://github.com/VisionForge-OU/foreman/compare/v0.4.8...v0.4.9
[0.4.8]: https://github.com/VisionForge-OU/foreman/compare/v0.4.7...v0.4.8
[0.4.7]: https://github.com/VisionForge-OU/foreman/compare/v0.4.6...v0.4.7
[0.4.6]: https://github.com/VisionForge-OU/foreman/compare/v0.4.5...v0.4.6
[0.4.5]: https://github.com/VisionForge-OU/foreman/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/VisionForge-OU/foreman/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/VisionForge-OU/foreman/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/VisionForge-OU/foreman/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/VisionForge-OU/foreman/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/VisionForge-OU/foreman/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/VisionForge-OU/foreman/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/VisionForge-OU/foreman/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/VisionForge-OU/foreman/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/VisionForge-OU/foreman/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/VisionForge-OU/foreman/releases/tag/v0.1.0
