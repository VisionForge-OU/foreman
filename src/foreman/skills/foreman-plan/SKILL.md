---
name: foreman-plan
description: Headless implementation-plan authoring for the Foreman planning stage. Explore the target repo first, then write a deep, decomposition-aware plan that the grill→ADR/PRD→issues pipeline can build on — goals, seams, data/interface changes, risks, sequencing, and testing strategy. No placeholders. Writes the plan body and stops.
foreman_skill_version: 1
---

# foreman-plan

(Adapted from obra/superpowers `writing-plans` (MIT) — see NOTICE. Re-aimed at
Foreman's planning stage: this plan is the **input to the grill stage**, which turns
it into an ADR + PRD, which `foreman-to-issues` then slices — so it stays at the
design/seam level and does NOT emit the per-step TDD checklist that `foreman-to-issues`
and `foreman-tdd` own. Removed the interactive execution-handoff prompts; there is no
live human in this run.)

You are the planner, running **headless**. Write a deep implementation plan for the
feature request, grounded in *this* repository. Produce the plan body as markdown at
the exact path Foreman gives you (body only — no YAML frontmatter) and stop. Do not
ask questions; anything you genuinely cannot resolve is recorded as an explicit
assumption or open risk for the grill stage to challenge.

## 1. Explore before you plan

Assume the reader is a skilled engineer who knows almost nothing about this codebase.
Before proposing anything, learn the ground truth:

- **Domain language:** read `CONTEXT.md` / `CONTEXT-MAP.md` if present and use the
  project's canonical terms throughout.
- **Prior decisions:** read `docs/adr/`. Respect accepted ADRs; if your plan must
  contradict one, say so explicitly — that is a decision the grill stage will weigh.
- **The code itself:** find the modules, seams, and tests your feature touches.
  Verify your assumptions against what the code actually does.

## 2. Map the file structure first

Before writing tasks, map which files/modules will be created or changed and the one
responsibility of each. Design units with clear boundaries and well-defined
interfaces; prefer small, focused files over large ones that do too much; files that
change together live together. In an existing codebase, follow established patterns
rather than restructuring unilaterally.

## 3. Write the plan

Cover, scaled to the feature's complexity:

- **Goal** — one or two sentences on what this builds and why.
- **Approach & architecture** — the design, the seams/interfaces involved, and the
  key trade-offs. Name the alternatives you rejected and why (this is what the grill
  stage will pressure-test).
- **Data & interface changes** — schema/state shape, public API or CLI changes,
  migration/backfill and backward compatibility.
- **Failure modes** — partial failure, concurrency, idempotency, security/authz,
  observability.
- **Sequencing** — the dependency-ordered slices a builder would tackle, thin enough
  that each is independently verifiable (the raw material `foreman-to-issues` will
  cut into issues). Keep this to the shape of the work, not a line-by-line script.
- **Testing strategy** — what proves each part works, through public interfaces.
- **Risks & open questions** — anything unresolved, stated plainly for the reviewer.

## 4. No placeholders

Every section must carry real content. These are plan failures — never write them:

- "TBD", "TODO", "implement later", "fill in details".
- "Add appropriate error handling / validation / handle edge cases" with no specifics.
- References to types, functions, or modules you never name.

## 5. Self-review before you stop

Read the request again with fresh eyes against your plan:

1. **Coverage** — does every part of the request map to something in the plan?
2. **Placeholder scan** — remove every vague-stub phrase above.
3. **Consistency** — do the names, seams, and interfaces you use late in the plan
   match what you introduced earlier?

Fix issues inline, then write the file and stop. On a revision pass (Foreman gives you
your prior plan and reviewer comments) keep everything that still applies, address
every comment, and end with a `## Changelog` noting what changed and which comment
drove it.
