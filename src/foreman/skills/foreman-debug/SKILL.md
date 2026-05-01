---
name: foreman-debug
description: Headless root-cause debugging loop for a Foreman worker whose tests, build, or acceptance check are failing — especially on a retry. Find the root cause before changing anything, fix at the source with a regression test, and never thrash on symptom patches. Used inside a foreman-tdd build session; emits no summary of its own.
foreman_skill_version: 1
---

# foreman-debug

(Adapted from obra/superpowers `systematic-debugging` (MIT) — see NOTICE. Made
headless: removed the "discuss with your human partner" hand-offs — under Foreman
there is no live human, so a genuine architectural dead end becomes a FOREMAN-SUMMARY
`escalate` from the surrounding foreman-tdd run, not a question. Folded the regression
step into Foreman's existing `foreman-test` + evidence contract.)

You are invoked **inside a foreman-tdd build session** when something is failing: a
red test that should be green, the project test/lint/typecheck command, the issue's
`acceptance_check`, or a distilled failure report from a prior attempt. You run
**headless** — do not ask questions. Find the root cause, fix it at the source, and
hand control back to foreman-tdd. You emit **no FOREMAN-SUMMARY** of your own; the
foreman-tdd run owns the single summary block.

## The Iron Law

```
NO FIX WITHOUT ROOT-CAUSE INVESTIGATION FIRST
```

A symptom patch that makes the red go away without explaining *why* it was red is a
failure — it will bounce at Foreman's merge gate or resurface on the next slice.

## The four phases (complete each before the next)

### 1. Root cause

- **Read the failure completely.** The exact assertion, the stack trace, the file and
  line, the `ERROR` lines in the `foreman-test` log on disk. The message often *is*
  the answer. If a distilled failure report from a prior attempt is in your context,
  treat its "why it was rejected" as the starting hypothesis, not noise to re-discover.
- **Reproduce deterministically.** Run the single failing test through `foreman-test`
  (use `--fast` while iterating). If it is flaky, that *is* the bug — chase the
  nondeterminism (ordering, time, shared state), don't paper over it.
- **Check what changed.** `git diff` the slice against the integration branch. The
  regression almost always lives in the diff.
- **Trace the bad value to its source.** Where does the wrong value originate? What
  passed it in? Keep walking *up* the call stack until you reach the origin. Fix
  there, not at the symptom.

### 2. Pattern

Find working code that does the same thing elsewhere in the repo. List **every**
difference between it and the broken path, however small — "that can't matter" is how
root causes hide.

### 3. Hypothesis

State one specific hypothesis: "the root cause is X because Y." Make the **smallest**
change that tests it. One variable at a time. If it doesn't hold, form a *new*
hypothesis — do not stack a second fix on top of an unproven first.

### 4. Fix at the source

1. **Lock it with a failing test first.** Add (or keep) a test that fails *because of
   this root cause* and will pass once it's fixed — exactly the red-green discipline
   foreman-tdd already uses. A fix with no test that proves it does not count.
2. **One change.** Address the root cause only — no "while I'm here" refactors.
3. **Verify through `foreman-test`.** The targeted test passes AND the full suite
   stays green. Read the output; do not assume.

## When 3+ fixes have failed

If three distinct fixes each fail or each surfaces a new problem somewhere else, the
issue is **architectural, not a bug** — the slice's seam is wrong. Stop patching.
Hand back to foreman-tdd with a clear note for its FOREMAN-SUMMARY: set `escalate:
true` with a one-line statement of the structural problem (e.g. "ISS-012 assumes a
synchronous store but the queue is async — the seam can't hold"). A wrong architecture
is Foreman's human's call, not another guess.

## Red flags — stop and return to phase 1

- "Quick fix now, understand it later."
- "Just try changing X and see."
- Bundling several changes, then running tests.
- Proposing a fix before you traced the bad value to its origin.
- A fourth fix attempt after three failures (→ escalate the architecture instead).
