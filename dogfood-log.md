# Foreman Dogfood Log

- Started a stability pass using `/home/arash/foreman-validation/notesapi`.
- Inspected Foreman and notesapi worktrees before changing files.
- Found existing Foreman feature state in notesapi under `.foreman/features`.
- Confirmed notesapi has dirty state from prior Foreman runs.
- Confirmed Foreman already uses Textual `run_test` for TUI checks.
- Confirmed `pytest-textual-snapshot` is not installed in the current environment.
- Added `pytest-textual-snapshot` to Foreman's dev dependencies.
- Installed Foreman dev dependencies with `uv pip install -e ".[dev]"`.
- Installed `pytest-textual-snapshot==1.0.0`.
- Ran `pytest tests/test_tui.py -q`.
- Found pyenv pytest could not load `pytest-asyncio`.
- Switched the test loop to uv's installed environment.
- Ran `uv run pytest tests/test_tui.py -q`.
- Confirmed the existing TUI suite passes under uv.
- Mounted Foreman TUI against notesapi real state.
- Opened dashboard, attention, review, metrics, and settings screens.
- Found no crash in that notesapi screen navigation pass.
- Added a notesapi-backed TUI dogfood test.
- Ran `uv run pytest tests/test_tui.py -q`.
- Confirmed 8 TUI tests pass.
- Ran `uv run pytest -q`.
- Confirmed 275 tests pass.
- Ran `uv run foreman status` in notesapi.
- Confirmed notesapi has one building feature and one done feature.
- Found no confirmed UI bug to file yet.
- User asked to continue dogfooding through the TUI instead of the build CLI.
- Checked notesapi after the interrupted CLI build command.
- Launched `uv run foreman` in notesapi as a real TUI.
- Observed dashboard issue ids render as `ISS-...` at default terminal width.
- Filed `issues/ISS-001-dashboard-kanban-truncates-issue-ids.md`.
- Changed the dashboard issue board to use a compact list in narrow panes.
- Added a regression assertion for full issue ids in the notesapi TUI test.
- Ran the focused notesapi TUI regression test.
- Ran the full TUI test file.
- Confirmed 8 TUI tests pass.
- Verified the 80-column TUI board prints full issue ids.
- Started a full test suite verification after the dashboard fix.
- Stopped monitoring the full suite when the user said it was stuck.
- Checked for leftover pytest processes and found none.

## H4–H7 checkpoint pass (TUI integration tests, 0.4.13)
- Continued CHECKPOINTS.md from H3 → tested H4, H5, H6, H7 via TUI/controller integration tests.
- H6: found B5 — rejecting a PRD amendment was a silent drop; `audit.fix_issue_bodies()` was wired nowhere.
- Fixed B5: `scheduler.reject_amendment` reloads the persisted audit, keeps the approved spec (strips the
  amendment + re-seals), and spins each divergence into a queued buildable `FIX-NNN` issue; wired through
  `controller.request_changes` + the TUI ReviewScreen; added `audit.report_from_raw`.
- H7: found B6 — no TUI path to review retro proposals (gate was CLI-only).
- Fixed B6: added `driver.reject`/`bench_report`/`list_names`, controller proposal surface, and a TUI
  RetroScreen (`t` from the dashboard) enforcing the approval+bench landing gate via notify errors.
- H4: found B7 — build report omitted retries; added `BuildReport.retries` rendered alongside cost/escalations.
- H4 machinery (parallel disjoint, dependent-waits, initializer-once, cost) already covered — re-verified.
- H5: added a no-monkeypatch end-to-end test — escalate → answer in AttentionScreen → real resume →
  the answer reaches the resumed worker's prompt (new session) → issue merges. No bug; gap closed.
- Updated DECISIONS.md (WS5 reject path now wired; WS6 RetroScreen + retries-in-report), RESULTS.md
  (H4–H7 → PASS), VALIDATION_BUGS.md (B5/B6/B7). Bumped to 0.4.13.
- Full suite: `uv run pytest -q` → 292 passed.
