# CONTEXT.md Format

(Adapted from mattpocock/skills `grill-with-docs/CONTEXT-FORMAT.md` — see NOTICE.)

`CONTEXT.md` is the **target repo's** domain glossary. It lives at the repo root
(single context) or per-context with a `CONTEXT-MAP.md` at the root (multi-context).

## Structure

```md
# {Context Name}

{One or two sentence description of what this context is and why it exists.}

## Language

**Order**:
{A one or two sentence description of the term}
_Avoid_: Purchase, transaction

**Invoice**:
A request for payment sent to a customer after delivery.
_Avoid_: Bill, payment request
```

## Rules

- **Be opinionated.** When multiple words exist for one concept, pick the best and
  list the others under `_Avoid_`.
- **Keep definitions tight.** One or two sentences. Define what it IS, not what it does.
- **Only project-specific terms.** General programming concepts don't belong.
- **Group under subheadings** when natural clusters emerge.
- `CONTEXT.md` is a glossary and nothing else — no implementation detail, no specs.

## Single vs multi-context repos

- If `CONTEXT-MAP.md` exists, read it to find each context's `CONTEXT.md`.
- If only a root `CONTEXT.md` exists, single context.
- If neither exists, create a root `CONTEXT.md` lazily when the first term is resolved.

When multiple contexts exist, infer which one the current topic relates to. If it
is genuinely unclear, raise it as an open question rather than guessing.
