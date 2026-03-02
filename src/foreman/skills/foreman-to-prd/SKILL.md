---
name: foreman-to-prd
description: PRD template and authoring rules for Foreman. Synthesizes a PRD from the approved plan and the grilled decisions and writes it as a local file in the Foreman feature directory. Does not interview the user and does not publish to any external issue tracker.
foreman_skill_version: 1
---

# foreman-to-prd

(Adapted from mattpocock/skills `to-prd` — see NOTICE. Removed: live "check with
the user" seam confirmation and the GitHub publish + `ready-for-agent` label step.
Output is a local `prd.md` file, not a tracker post.)

This skill is the **PRD template authority**. The `foreman-grill-docs` skill calls
it to produce `prd.md`. Synthesize from the approved plan, the codebase, and the
grilled decisions — do NOT interview anyone.

## Process

1. Explore the target repo to understand the current state, if you haven't. Use
   the project's domain glossary (`CONTEXT.md`) throughout, and respect ADRs in
   the area you're touching.

2. Identify the **seams** at which the feature will be tested. Prefer existing
   seams; use the highest seam possible. If a new seam is needed, propose it at
   the highest point you can — and if whether that seam is acceptable is a genuine
   product/architecture call you cannot settle from the code, add it to the
   `## Open questions for reviewer` block rather than asking interactively.

3. Write `prd.md` into the feature directory using the template below. Begin the
   file with the open-questions block (see `foreman-grill-docs`). Do not publish
   anywhere and do not apply any labels.

<prd-template>

## Open questions for reviewer

<unresolved product/architecture questions, or "_None — all resolved._">

## Problem Statement

The problem the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A LONG, numbered list of user stories, each: `As an <actor>, I want a <feature>,
so that <benefit>`. Extremely extensive — cover all aspects of the feature. These
stories are the basis for the slicer (`foreman-to-issues`) and for the e2e flows,
so make each one concrete and verifiable.

## User Flows

For each end-to-end flow a user can perform, list the ordered steps and the
observable outcome. These flows are what Foreman's e2e phase will turn into
automated tests, so be precise about preconditions, steps, and expected results.

## Implementation Decisions

Modules built/modified, interfaces changed, technical clarifications,
architectural decisions, schema changes, API contracts, specific interactions. No
file paths or code snippets (they go stale) — *exception*: a prototype-derived
snippet that encodes a decision more precisely than prose (state machine, reducer,
schema, type shape) may be inlined, trimmed to the decision-rich parts.

## Testing Decisions

What makes a good test here (test external behavior, not implementation details);
which modules will be tested; prior art for the tests (similar tests already in
the codebase); the test/lint/typecheck commands Foreman will run to verify work.

## Out of Scope

What is explicitly not part of this PRD.

## Further Notes

Anything else worth recording.

</prd-template>
