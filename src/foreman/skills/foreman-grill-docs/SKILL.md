---
name: foreman-grill-docs
description: Headless grilling pass that challenges an approved implementation plan against the existing codebase and domain model, then writes an ADR draft and a PRD draft into the Foreman feature directory. Self-answers every question it can from the code/docs and surfaces the rest as an "Open questions for reviewer" block instead of interviewing a live user.
foreman_skill_version: 2
---

<what-to-do>

You are running **headless, with no live human in the loop.** Your job is to grill
the approved implementation plan as hard as a senior engineer would in a live
design review — but instead of asking the human questions one at a time, you
**self-answer everything you can** and **defer only what you genuinely cannot
resolve** to a written review gate.

Walk the full design decision tree. For each branch:

1. **Try to answer it yourself first** by exploring the target codebase, its
   `CONTEXT.md` / `CONTEXT-MAP.md` glossary, and its `docs/adr/` log. If the code
   or the docs settle the question, record the answer in the draft and move on.
2. **Only if it is genuinely unresolvable** from available evidence — because it
   needs a product call, a priority trade-off, or knowledge that lives only in the
   reviewer's head — add it to the `## Open questions for reviewer` block at the
   very top of the relevant draft.

Do NOT ask the user anything interactively. Do NOT wait for input. Produce the
drafts and stop.

</what-to-do>

<inputs>

Foreman injects, in the prompt:

- The path to the approved `plan.md`.
- The feature directory `.foreman/features/<slug>/` where you must write `adr.md`
  and `prd.md`.
- On a **revision pass**, the previous `adr.md`/`prd.md` and the reviewer's
  comments (which double as answers to the previous open questions).

</inputs>

<process>

### 1. Explore before you challenge

Read the plan. Then explore the target repo to ground every claim:

- Domain language: read `CONTEXT.md` (or follow `CONTEXT-MAP.md` to the right
  context). Use the project's canonical terms throughout both drafts. If the plan
  uses a term that conflicts with the glossary, flag the conflict and prefer the
  glossary term.
- Prior decisions: read `docs/adr/`. Respect accepted ADRs; if the plan
  contradicts one, that is a finding — either the plan is wrong or a new ADR
  supersedes the old one. Say which.
- The code itself: verify the plan's assumptions about how things currently work.
  When the plan says "X works like Y", check whether the code agrees. Surface
  every contradiction.

### 2. Grill across every dimension

Challenge the plan on: domain-model fit, data/schema shape, failure modes and
partial failure, concurrency, idempotency, security and authorization, migration
and backfill, observability, testability and seams, performance envelope,
backward compatibility, and rollout/rollback. For each dimension, either resolve
it in the draft or raise an open question.

Stress-test relationships with concrete scenarios. Invent specific edge-case
scenarios and force the boundaries between concepts to be precise.

### 3. Update the target repo's living docs inline

As decisions crystallise, update the target repo's own documentation **right
there**, not in a batch:

- Sharpen or add glossary terms in `CONTEXT.md` using `CONTEXT-FORMAT.md`. Create
  `CONTEXT.md` lazily (only once the first term is worth recording). It is a
  glossary only — never implementation detail.
- Record genuinely architectural, hard-to-reverse, trade-off decisions as ADRs in
  `docs/adr/` using `ADR-FORMAT.md`. Offer ADRs sparingly (all three tests in
  ADR-FORMAT must hold). Create `docs/adr/` lazily.

### 4. Write the two Foreman drafts

Write `adr.md` and `prd.md` into the feature directory. Each begins with the open
questions block (see format below). The ADR draft captures the architectural
decision narrative for this feature; the PRD draft follows the PRD template in the
`foreman-to-prd` skill (problem, solution, user stories, implementation
decisions, testing decisions, out of scope, further notes).

In the **PRD draft only**, immediately after the open-questions block and before
the prose, emit a `## Decisions made on your behalf` section: **≤10 bullets**, each
a single audit-judgment call you settled autonomously that the reviewer should be
able to sanity-check **without re-reading the prose** (e.g. "Chose optimistic
locking over a mutex because the contention window is sub-millisecond"). These are
decisions you *made* and resolved — distinct from the open questions you *deferred*.
Keep each bullet to one line; omit the section only if you genuinely made no
non-obvious calls (then write a single line: `_None — no judgment calls beyond the
plan._`).

### 5. On a revision pass

When Foreman re-runs you with reviewer comments:

- Treat each reviewer comment as the answer to an open question (or as new
  guidance). Resolve that branch of the decision tree and fold the resolution into
  the body.
- Remove every question the comments answered from the open-questions block.
- Surface any **newly uncovered** questions that the answers expose.
- Append a short `## Changelog` entry at the bottom of each revised draft noting
  what changed in this version and which comments drove it.

</process>

<open-questions-format>

At the very top of BOTH `adr.md` and `prd.md`, immediately after the title:

```md
## Open questions for reviewer

- <a question you genuinely could not resolve from code/docs>
- <another>
```

Rules:

- This block is the **only** way you communicate with the reviewer. Put nothing
  here you could have answered yourself.
- If you resolved everything, write the block with a single line:
  `_None — all questions resolved from the codebase and prior decisions._`
- Foreman will not let the reviewer approve a draft while any unresolved bullet
  remains. The grill loop is complete only at **zero open questions AND reviewer
  approval.**
- A question the reviewer has answered (via a comment) must be **removed**, not
  left struck through, on the next pass.

</open-questions-format>

<supporting-info>

- ADR format: [ADR-FORMAT.md](./ADR-FORMAT.md)
- CONTEXT.md format: [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md)
- PRD template authority: the `foreman-to-prd` skill.

</supporting-info>
