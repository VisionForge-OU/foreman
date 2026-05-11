# Cost ledger

Every worker/agent/judge run appended as it completes. `real` = real `claude` tokens spent; `mock` = MockBackend (free).

| when | feature | label | source | model | real? | cost_usd | turns | note |
|------|---------|-------|--------|-------|-------|----------|-------|------|
| 2026-06-15T21:25:22Z | add-done-command | planner | foreman-worker | mock | mock | 0.0600 | 2 | completed |
| 2026-06-15T21:25:22Z | add-done-command | grill | foreman-worker | mock | mock | 0.0800 | 3 | completed |
| 2026-06-15T21:25:22Z | add-done-command | grill | foreman-worker | mock | mock | 0.0800 | 3 | completed |
| 2026-06-15T21:25:22Z | add-done-command | slicer | foreman-worker | mock | mock | 0.0500 | 2 | completed |
| 2026-06-15T21:25:22Z | add-done-command | init | foreman-worker | mock | mock | 0.0200 | 2 | completed |
| 2026-06-15T21:25:22Z | add-done-command | ISS-001 | foreman-worker | mock | mock | 0.1000 | 4 | completed |
| 2026-06-15T21:25:22Z | add-done-command | ISS-001 | foreman-worker | mock | mock | 0.1200 | 5 | success_after_retry(2) |
| 2026-06-15T21:25:22Z | add-done-command | ISS-001-eval | foreman-worker | mock | mock | 0.0100 | 2 | completed |
| 2026-06-15T21:25:22Z | add-done-command | ISS-002 | foreman-worker | mock | mock | 0.1200 | 5 | success_first_try |
| 2026-06-15T21:25:22Z | add-done-command | ISS-002-eval | foreman-worker | mock | mock | 0.0100 | 2 | completed |
