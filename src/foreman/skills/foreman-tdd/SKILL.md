---
name: foreman-tdd
description: Stack-agnostic test-driven development loop for a single Foreman issue.
  Implements one vertical slice with strict red-green-refactor (one test at a time,
  never horizontal slicing) using the foreman-test wrapper, saves completion evidence,
  then emits a machine-readable FOREMAN-SUMMARY block Foreman parses.
foreman_skill_version: 3
---

# foreman-tdd

(Adapted from mattpocock/skills `tdd` — see NOTICE. Made stack-agnostic: test,
lint and typecheck commands are injected by Foreman from `config.yaml`, not
hard-coded to npm/Husky. Removed interactive "confirm with the user / get user
approval" steps — those become escalation triggers. Added the FOREMAN-SUMMARY
output block.)

## Inputs (injected by Foreman in the prompt)

- The full **issue file** (`ISS-NNN.md`) — its Goal, Acceptance criteria, Out of
  scope, `prd_refs`, and its `acceptance_check` (a runnable check Foreman re-runs
  independently — your slice is not done until it passes). This is your slice
  definition.
- The **commands** for the project (`test`, `lint`, `typecheck`) — but you run
  tests through the **`foreman-test`** wrapper (see below), never the raw runner.
- The target repo conventions (`CONTEXT.md`, relevant ADRs).
- The **evidence directory** (`runs/<id>/evidence/`) you MUST populate before
  claiming done.
- On a retry, the **failing output** from the previous attempt.

You run headless in a git worktree on the issue's branch, cwd set to that
worktree. Implement the slice and stop. Do not ask for confirmation.

## The foreman-test wrapper (use it exclusively)

Run tests with **`foreman-test`** (on your PATH) instead of the raw test runner:
- `foreman-test` — full suite, quiet output (counts + failures only), full log
  on disk with greppable `ERROR` lines.
- `foreman-test --fast` — a deterministic per-worker random subsample for cheap
  inner-loop runs. Use this while iterating; run the **full** `foreman-test`
  before you finish. Foreman re-runs the full suite itself regardless.

**Wall-clock discipline:** the wrapper prints elapsed time. Spend at most ~1 turn
in 3 re-running tests; the rest goes to making changes. Don't loop on the runner.

## You may NOT write Foreman-owned state

`verification.json`, any issue file, and the canonical `*.check/` artifacts are
Foreman's. A worktree hook will block (and surface) any attempt to write them —
do not try. Foreman decides "done", not you.

## Philosophy

**Tests verify behavior through public interfaces, not implementation details.**
Good tests are integration-style: they exercise real code paths through public
APIs and read like a specification ("user can checkout with valid cart"). They
survive refactors. Bad tests mock internal collaborators, assert on call
counts/order, or verify through external means. See [tests.md](./tests.md).

## Anti-pattern: horizontal slices

**DO NOT write all tests first, then all implementation.** That produces tests of
*imagined* behavior. Work vertically: one test → its implementation → repeat. Each
test responds to what you learned from the previous cycle.

```
WRONG (horizontal):  RED: test1..test5   then   GREEN: impl1..impl5
RIGHT (vertical):    RED→GREEN: test1→impl1 ; test2→impl2 ; test3→impl3 ; ...
```

## Workflow

### 1. Plan from the issue

Derive the behaviors to test from the issue's **Acceptance criteria**. Use the
project's domain glossary for test and interface names. Identify the public
interface/seam for the slice and design it for testability (small interface, deep
implementation). List the behaviors — not implementation steps. You set this plan
yourself; there is no user to approve it. If a criterion is ambiguous or
contradicts an ADR or the codebase such that you cannot proceed safely, STOP and
emit a FOREMAN-SUMMARY with `escalate: true` and the specific question (Foreman
routes it to the human attention queue).

### 2. Tracer bullet

Write ONE test for the first behavior → run the test command → it fails (RED).
Write the minimal code to pass → run again → it passes (GREEN). This proves the
path end-to-end.

### 3. Incremental loop

For each remaining acceptance criterion: RED (one new test, fails) → GREEN
(minimal code, passes). One test at a time. Only enough code to pass the current
test. Don't anticipate future tests. Keep tests on observable behavior.

### 4. Refactor (only while GREEN)

After all tests pass: extract duplication, deepen modules, apply SOLID where
natural, run the test command after each refactor step. **Never refactor while
RED.**

### 5. Verify, save evidence, and summarise

Run the full `foreman-test`, then `lint`, then `typecheck` (whichever exist) and
capture each command's pass/fail and a short output tail. Confirm the issue's
`acceptance_check` passes.

**Completion contract (required):** before claiming done, save evidence artifacts
proving you observed success into the evidence directory Foreman gave you
(`runs/<id>/evidence/`) — at minimum the test log, plus command outputs (and a
screenshot for UI work via the configured e2e tooling). List each saved artifact
in the FOREMAN-SUMMARY `evidence` array. **A "complete" claim with missing or
empty evidence is rejected and counts as a failed attempt** — Foreman validates
the evidence on disk and re-runs every command itself; it does not trust claims.

## Required output: FOREMAN-SUMMARY

End every run with exactly one fenced block tagged `json` whose content is a
single JSON object on the schema below. Nothing after it.

````md
```json
{
  "schema": "foreman-summary/v1",
  "issue_id": "ISS-001",
  "files_touched": ["path/a", "path/b"],
  "tests_added": ["describe/it name or test function name", "..."],
  "commands": {
    "test":      {"ran": true,  "passed": true,  "output_tail": "...last lines..."},
    "lint":      {"ran": true,  "passed": true,  "output_tail": "..."},
    "typecheck": {"ran": false, "passed": null,  "output_tail": "not configured"}
  },
  "evidence": ["test.log", "acceptance.log"],
  "open_concerns": ["anything you are unsure about"],
  "escalate": false,
  "escalation_question": "",
  "request_more_turns": 0
}
```
````

- `evidence` lists the artifacts you saved under `runs/<id>/evidence/`. It must be
  non-empty for a completion claim; Foreman rejects an empty/unbacked claim.
- `escalate: true` + a non-empty `escalation_question` means you could not finish
  safely and need a human decision; set it instead of guessing.
- `request_more_turns: N` (a small positive integer) means you are making real
  progress but cannot finish this slice within your turn budget. Set it INSTEAD of
  letting Foreman cut you off, and do **not** also set `escalate`. Write your
  progress.md handoff first. Foreman may grant a bounded extension and **resume this
  same session** so you continue where you left off. Use `escalate` (not this) for
  genuine blockers; leave this `0` when you finish normally.
- `commands[*].passed` is your honest result; Foreman verifies independently.
