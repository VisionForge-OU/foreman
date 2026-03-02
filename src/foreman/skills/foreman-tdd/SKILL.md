---
name: foreman-tdd
description: Stack-agnostic test-driven development loop for a single Foreman issue. Implements one vertical slice with strict red-green-refactor (one test at a time, never horizontal slicing) using the test/lint/typecheck commands supplied by Foreman, then emits a machine-readable FOREMAN-SUMMARY block Foreman parses.
foreman_skill_version: 1
---

# foreman-tdd

(Adapted from mattpocock/skills `tdd` — see NOTICE. Made stack-agnostic: test,
lint and typecheck commands are injected by Foreman from `config.yaml`, not
hard-coded to npm/Husky. Removed interactive "confirm with the user / get user
approval" steps — those become escalation triggers. Added the FOREMAN-SUMMARY
output block.)

## Inputs (injected by Foreman in the prompt)

- The full **issue file** (`ISS-NNN.md`) — its Goal, Acceptance criteria, Out of
  scope, and `prd_refs`. This is your slice definition.
- The **commands** to use: `test`, `lint`, `typecheck` (any may be absent — skip
  the absent ones). Use exactly these; do not invent your own.
- The target repo conventions (`CONTEXT.md`, relevant ADRs).
- On a retry, the **failing output** from the previous attempt.

You run headless in a git worktree on the issue's branch, cwd set to that
worktree. Implement the slice and stop. Do not ask for confirmation.

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

### 5. Verify and summarise

Run the configured `test`, then `lint`, then `typecheck` commands (whichever
exist) and capture each command's pass/fail and a short tail of its output. Then
emit the summary block below as the LAST thing in your output.

Foreman re-runs these same commands itself and does not trust your claims — but an
honest, accurate summary lets it cross-check and is required.

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
  "open_concerns": ["anything you are unsure about"],
  "escalate": false,
  "escalation_question": ""
}
```
````

- `escalate: true` + a non-empty `escalation_question` means you could not finish
  safely and need a human decision; set it instead of guessing.
- `commands[*].passed` is your honest result; Foreman verifies independently.
