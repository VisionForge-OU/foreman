---
id: ISS-001
title: Dashboard issue board truncates issue ids in default terminal width
status: done
source: notesapi TUI dogfood
---

## Problem

Running `uv run foreman` in `/home/arash/foreman-validation/notesapi` at the default PTY size shows the dashboard issue board with every issue id truncated to `ISS-...`.

## Impact

The user cannot tell `ISS-001`, `ISS-002`, and `ISS-003` apart from the main dashboard, which makes the build queue and attention state hard to operate from the TUI.

## Acceptance Criteria

- The dashboard preserves full issue ids at default 80-column terminal width.
- Wider terminals may continue to use the existing table layout.
- A TUI regression test covers the notesapi dogfood state.

## Resolution

The dashboard now switches to a compact status summary when the right pane is too narrow for the six-column table. Verified at 80 columns with the notesapi dogfood state.
