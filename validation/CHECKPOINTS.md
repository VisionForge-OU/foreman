# Human Checkpoints — live-TUI handoff

I (the conductor) cannot drive the interactive TUI. These checkpoints need you at a real
terminal. Setup once, then walk H1→H7. Reply `done + observations` after each; UX friction
is logged to `VALIDATION_BUGS.md` as `[dx]`. Machinery under each checkpoint is already
validated on the mock backend (Steps 4–5); these validate **agent quality + ergonomics**.

## Setup (Scenario A — "Add tagging to notes")
```
cd ~/foreman-validation/notesapi
foreman                       # launches the TUI (real agents; haiku worker, $1.50/run cap)
```
Submit feature request titled **Add tagging to notes**:
> Notes can carry tags. `POST /notes` accepts an optional `tags: [str]`. `GET /notes?tag=x`
> filters to notes with that tag. A new `GET /tags` lists distinct tags with counts.

Cost guardrails are already set in `.foreman/config.yaml` (haiku, max_turns 30, max_cost 1.50).
Stop and tell me if cumulative spend approaches **$15**.

---

### === CHECKPOINT H1 — Plan review + revise loop ===
**What to do:** When the planner produces `plan.md`, open it. **Request changes once** (add a
comment, e.g. “split the model change from the endpoint change”), submit, let it revise, then **approve**.
**What to look for:** the revision (a) consumes your comment, (b) appends a changelog entry, (c) bumps
the version (v1→v2). Approval only available at the revised version.
**Reply with:** done + did the changelog/version-bump appear? any friction?

### === CHECKPOINT H2 — Grill: ADR + PRD, open questions ===
**What to do:** After grill produces `adr.md` + `prd.md`, find the **“Open questions for reviewer”**
section and the **“decisions made on your behalf”** digest. Answer the open question(s) via review
comments, then approve both docs.
**What to look for:** the next revision resolves your answers; you can only approve at **zero open
questions** (approval is blocked while any remain). Both ADR and PRD seal on approval.
**Reply with:** done + were open-questions + decisions-digest both present? did approve block until zero open Qs?

### === CHECKPOINT H3 — Queue review (slices + conflict graph) ===
**What to do:** Review the sliced issues queue, then confirm it.
**What to look for:** every issue shows frontmatter per schema (`prd_refs`, `touches`, a runnable
`acceptance_check`); the queue screen shows each check and the **conflict graph** (the model+POST issue
vs the `/tags` issue should be disjoint; the filter issue depends on the model change).
**Reply with:** done + did all issues have acceptance checks + touches? was the conflict graph shown?

### === CHECKPOINT H4 — Post-build audit + final report ===
**What to do:** Let the build run (watch streaming + budget meters), then review the e2e/auditor results
and the final feature summary report.
**What to look for:** initializer ran once (init.sh, feature-state.md); the two disjoint issues ran in
**parallel in separate worktrees** with independent budget meters; the dependent issue waited;
`foreman-test` output ≤20 lines + ERROR-greppable log; evaluator verdicts (rubric JSON); Foreman
re-ran tests itself; merges to integration; final report has cost / retries / escalations.
**Reply with:** done + did the two disjoint issues visibly run in parallel? report numbers sane?

---

## Scenario B faults needing your eyes (machinery already PASS in Step 4)
H5/H6/H7 below validate the **human side** of faults whose machinery is already green.

### === CHECKPOINT H5 — Escalation answer consumed on resume (F7) ===
Machinery PASS (fault_matrix F7: fresh-session retry carries a distilled report; `resume_issue`
consumes the answer and logs “Reviewer answer”). **What to do:** in a build that escalates an issue to
`needs_human`, answer the escalation in the TUI. **Look for:** the resume/re-spawn consumes your answer
(new session) and proceeds. **Reply with:** done + did your answer reach the new session?

### === CHECKPOINT H6 — Reject a PRD amendment → fix issues ===
Machinery PASS (Step 5.2: auditor divergence drafts a PRD amendment that re-enters review). **What to do:**
when the auditor drafts a PRD amendment (or force one via a hard-vs-soft-delete divergence), **reject** it.
**Look for:** rejection turns the amendment into **fix issues** (not a silent drop). **Reply with:** done +
did rejection create fix issues?

### === CHECKPOINT H7 — Review one retro proposal end-to-end ===
Machinery PASS (Step 5.4: proposals drafted gated `in_review`; bench delta attaches; landing blocked
without approval+bench). **What to do:** `foreman retro` then open one proposal in review; inspect the diff
+ attached bench delta; **reject** it (or approve to test landing). **Look for:** the patch-approval gate —
nothing lands without your approval AND a bench report. **Reply with:** done + was the gate enforced?
