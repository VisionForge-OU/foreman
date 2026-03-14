# Foreman Validation — Cost Report

Hard ceiling: **$15.00** total. Per-run caps: `max_turns ≤ 30`, `max_cost_usd ≤ 1.50`.
notesapi config overridden to `model_planner/worker = claude-haiku-4-5` for cost control.

| Step | Backend | Real tokens? | Est. cost | Running total |
|------|---------|--------------|-----------|---------------|
| 0 Preflight (claude probe `PONG`) | real | yes (trivial) | ~$0.00 | $0.00 |
| 0 Foreman test suite (234 passed) | n/a | no | $0.00 | $0.00 |
| 1 Build notesapi | n/a | no | $0.00 | $0.00 |
| 2 foreman init + skill-gate | n/a | no | $0.00 | $0.00 |
| 5.5 `foreman demo` (mocked) | mock | no | $0.00 | $0.00 |
| 4 Fault matrix F1–F12 (`fault_matrix.py`, `run_f11.py`) | mock + real fns | no | $0.00 | $0.00 |
| 5 Janitor/divergence/metrics/retro/bench (`step5.py`,`step54.py`,`foreman retro`) | mock | no | $0.00 | $0.00 |

**Spend so far: ~$0.00** (one trivial haiku probe call; everything else mock/programmatic).

All machinery validation (Steps 4 & 5, the bulk of the exercise) ran on the mock
backend / real Foreman functions at **zero token cost** — leaving the entire $15
ceiling available for Scenario A's real-agent happy path (Step 3) when driven via the TUI.

_Updated as steps complete. Mock/programmatic steps cost $0; only Scenario A real-agent
runs and any real spot-checks draw down the ceiling._
