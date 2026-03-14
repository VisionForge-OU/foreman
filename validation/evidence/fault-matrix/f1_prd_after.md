---
kind: prd
version: 2
status: in_review
---

# PRD: `todo done` command

## Open questions for reviewer

_None — all questions resolved (re-completing is a silent no-op, per reviewer)._

## Problem Statement
Users can add and list todos but cannot mark them done.

## Solution
A `todo done <id>` command marks the item complete.

## User Stories
1. As a user, I want to mark a todo done, so that it stops nagging me.
2. As a user, I want a clear error if the id does not exist, so that I can retry.

## User Flows
1. Mark done: given a todo with id 1, when I run `todo done 1`, then it is marked
   completed and the command reports success.

## Implementation Decisions
Add `mark_done(id)` to the store; the CLI dispatches `done` to it.

## Testing Decisions
Test `mark_done` at the store interface; one CLI-level test for the happy path.
Commands: test=`pytest`.

## Out of Scope
Un-completing an item. Re-completing is a silent no-op.

## Further Notes
None.

## Changelog

- v2: resolved the re-completion question (no-op).
<!-- hand-edited one line post-approval -->
