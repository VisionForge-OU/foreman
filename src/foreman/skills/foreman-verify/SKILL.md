---
name: foreman-verify
description: Headless self-verification gate a Foreman worker runs before it claims an issue is done. Re-run the real commands, read the actual output, and only then write the FOREMAN-SUMMARY — evidence before claims, always. Used inside a foreman-tdd build session; emits no summary of its own.
foreman_skill_version: 1
---

# foreman-verify

(Adapted from obra/superpowers `verification-before-completion` (MIT) — see NOTICE.
Made headless and wired to Foreman's trust boundary: the "claim" you are gating is the
foreman-tdd FOREMAN-SUMMARY block and its `evidence` array, and the verification
commands are the project's own, run through the `foreman-test` wrapper. Foreman
re-runs every command itself regardless, so a dishonest claim is not just wrong — it
is rejected and counts as a failed attempt.)

You are invoked **inside a foreman-tdd build session**, right before it would claim
the slice is complete. Your job is to make that claim *true and evidenced*. You run
**headless** and emit **no FOREMAN-SUMMARY** of your own — you populate the evidence
the surrounding foreman-tdd run reports.

## The Iron Law

```
NO COMPLETION CLAIM WITHOUT FRESH VERIFICATION EVIDENCE
```

If you have not run the verifying command **in this session** and read its output,
you may not claim it passes. "Should pass", "I'm confident", "it worked earlier" are
not evidence.

## The gate function

For **every** claim the FOREMAN-SUMMARY will make (tests pass, lint clean, typecheck
clean, the issue's `acceptance_check` passes, the behaviour works):

1. **Identify** the exact command that proves it.
2. **Run** it fresh and in full — the full `foreman-test` suite (not just `--fast`),
   then `lint`, then `typecheck` if configured, then the `acceptance_check`.
3. **Read** the whole output: exit status, failure count, the `ERROR` lines.
4. **Save** the output as an evidence artifact under the run's evidence directory
   Foreman gave you (the test log at minimum, plus each command's output tail, plus a
   screenshot for UI work via the configured e2e tooling).
5. **Reconcile** the claim with the output. If it does not pass, the honest result is
   *not done* — let foreman-tdd keep working or, for a real blocker, escalate. Never
   round a failure up to a pass.

## What counts (and what doesn't)

| Claim | Requires | Not sufficient |
|-------|----------|----------------|
| Tests pass | full `foreman-test`: 0 failures, saved log | a `--fast` subsample, a previous run |
| Lint / typecheck clean | the command's own output: 0 errors | "the diff looks clean" |
| Acceptance check passes | running the issue's `acceptance_check` | the unit tests passing |
| Bug fixed | the original failing symptom now passes | the code changed |
| Regression test real | red→green proven (it failed before the fix) | it passes once now |

## Output

You do not write the summary — you guarantee it can be written honestly. Hand back to
foreman-tdd with: the verification commands run, their pass/fail and output tails, and
the exact list of evidence artifacts you saved (which becomes the FOREMAN-SUMMARY
`evidence` array). An empty or unbacked evidence array is rejected by Foreman — so if
you could not produce real evidence, say so plainly rather than claiming done.

## Red flags — stop

- Any wording implying success ("done", "great", "should be good") before the command
  has actually run in this session.
- Saving an empty evidence directory and reporting success anyway.
- Trusting a sub-agent's or a prior session's "it passed".
- Verifying a subset and extrapolating to the whole.
