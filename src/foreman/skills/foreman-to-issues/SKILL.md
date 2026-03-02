---
name: foreman-to-issues
description: Break an approved PRD into small, dependency-ordered, vertically-sliced implementation issues written as local files in the Foreman feature directory. No GitHub, no live quizzing of the user — emits files matching Foreman's issue schema with PRD traceability.
foreman_skill_version: 1
---

# foreman-to-issues

(Adapted from mattpocock/skills `to-issues` — see NOTICE. Removed: all `gh` CLI
usage, GitHub labels/triage vocabulary, and the interactive "quiz the user" loop.
Issues are emitted as local files; the human reviews them in Foreman's queue-review
screen instead.)

Break the **approved PRD** into independently-grabbable issues using vertical
slices (tracer bullets). Run headless: produce the files and stop. The human will
reorder/edit/delete/add in Foreman's queue-review screen — do not ask them
anything here.

## Process

### 1. Gather context

Read the approved `prd.md` (path injected by Foreman) — its body, user stories,
and user flows. Read the approved `plan.md` and `adr.md` too for decisions.

### 2. Explore the codebase

Understand the current state so titles and descriptions use the project's domain
glossary (`CONTEXT.md`) and respect ADRs in the area you're touching.

### 3. Draft vertical slices

Break the PRD into **tracer-bullet** issues. Each issue is a thin vertical slice
that cuts through ALL integration layers end-to-end (schema → logic → API → UI →
tests), NOT a horizontal slice of one layer.

<vertical-slice-rules>
- Each slice delivers a narrow but COMPLETE path through every layer.
- A completed slice is demoable or verifiable on its own.
- Prefer many thin slices over few thick ones.
- Order slices by dependency: a slice that others build on comes first.
- Every slice traces back to one or more PRD sections / user stories.
</vertical-slice-rules>

### 4. Emit one file per slice

Write each slice to `.foreman/features/<slug>/issues/ISS-NNN.md`, numbered from
`001` in dependency order (blockers first, so `depends_on` can reference real
ids). Each file is YAML frontmatter + markdown body in **exactly** this schema:

```md
---
id: ISS-001
title: <short descriptive name>
status: queued
depends_on: []            # list of blocking issue ids, e.g. ["ISS-001"]
branch: feature/<slug>/iss-001
attempts: 0
budget: { max_turns: 80, max_cost_usd: 5.00, timeout_min: 45 }
prd_refs: ["PRD §<section>", "Story #<n>"]   # traceability back to the PRD
---
## Goal

A concise description of this vertical slice — the end-to-end behavior, not a
layer-by-layer implementation plan. Avoid file paths and code snippets (they go
stale); a prototype-derived decision snippet may be inlined if it encodes a
decision more precisely than prose.

## Acceptance criteria (testable)

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Out of scope

- What this slice deliberately does not do.
```

Rules:

- `id` is `ISS-` + zero-padded three-digit number, unique within the feature.
- `branch` is `feature/<slug>/iss-NNN` (lowercase).
- `budget` defaults come from the feature's config `run_budget`; only deviate when
  a slice is clearly bigger or smaller, and say why in the body if you do.
- `prd_refs` MUST be present and non-empty — every issue traces to the PRD.
- `depends_on` MUST be acyclic and reference only earlier issues.

Do not create any external tickets. Do not modify the PRD.
