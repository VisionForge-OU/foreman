# ADR Format

(Adapted from mattpocock/skills `grill-with-docs/ADR-FORMAT.md` — see NOTICE.)

ADRs live in the **target repo's** `docs/adr/` and use sequential numbering:
`0001-slug.md`, `0002-slug.md`, etc. Create `docs/adr/` lazily — only when the
first ADR is needed.

## Template

```md
# {Short title of the decision}

{1-3 sentences: what's the context, what did we decide, and why.}
```

That's it. An ADR can be a single paragraph. The value is in recording *that* a
decision was made and *why* — not in filling out sections.

## Optional sections

Only when they add genuine value (most ADRs won't need them):

- **Status** frontmatter (`proposed | accepted | deprecated | superseded by ADR-NNNN`)
- **Considered Options** — only when the rejected alternatives are worth remembering
- **Consequences** — only when non-obvious downstream effects need calling out

## Numbering

Scan `docs/adr/` for the highest existing number and increment by one.

## When to offer an ADR

All three must be true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful.
2. **Surprising without context** — a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one.

If a decision is easy to reverse, not surprising, or had no real alternative, skip it.

### What qualifies

- **Architectural shape** ("the write model is event-sourced, read model projected to Postgres").
- **Integration patterns between contexts** ("Ordering and Billing communicate via domain events").
- **Technology choices that carry lock-in** (database, message bus, auth provider, deploy target).
- **Boundary and scope decisions** ("Customer data is owned by the Customer context; reference by ID only").
- **Deliberate deviations from the obvious path** ("manual SQL instead of an ORM because X").
- **Constraints not visible in the code** ("response times under 200ms because of the partner API contract").
- **Rejected alternatives when the rejection is non-obvious.**

Because Foreman runs headless, you decide whether an ADR qualifies and write it
directly — you do not ask. If the call itself is uncertain, raise it as an open
question instead of guessing.
