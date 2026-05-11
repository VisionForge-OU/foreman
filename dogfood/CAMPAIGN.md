# Foreman self-driving campaign — design (C0)

**What this is:** an unattended, end-to-end soak test of Foreman's Phase 1+2
pipeline. A bespoke harness drives the *real* Foreman TUI through Textual Pilot
and plays the reviewer at every gate with a genuine rubric. It is **not** a test
of the human review experience — findings about machinery / agent quality / the
TUI are real; findings about review DX and trust are synthetic and deferred to a
future attended run.

## Environment facts (verified)
- Foreman v0.6.0 (HEAD `9e27d8a`), Python 3.13, Textual 8.2.7. Baseline suite:
  **368 passed** (offline, ~5 min).
- Real `claude` headless works here: a capped probe returned
  `total_cost_usd=0.0229`, `terminal_reason=completed`. Auth via
  `~/.claude/.credentials.json` (subscription OAuth); no `ANTHROPIC_API_KEY`.
- All Foreman worker flags are supported by the installed `claude` 2.1.174
  (`--effort`, `--max-budget-usd`, `--agent`, `--output-format stream-json`, …).
- **Cost-floor finding (real):** that trivial probe cost $0.023, almost entirely
  `cache_creation (10.4k tok) + cache_read (16.6k tok)` from the user's global
  `SessionStart` hooks + global context re-loading on every `claude -p`. Every
  worker subprocess pays this floor. Tracked explicitly in the ledger.

## Project & stack
**dayplan** — a small self-hostable productivity backend (HTTP API + SQLite) for
a daily-planning workflow. Stack = **Python + FastAPI + SQLite + pytest**, which
is what Foreman's defaults handle most reliably (its own verification command is
`pytest`). Scratch location: `~/foreman-dogfood/dayplan/` (never inside Foreman's
git tree). A minimal working seed (tasks: create/list, SQLite, created_at stored
but **not** returned) gives brownfield/trivial features a real base to modify.

## Backlog (5 features by type)
| key | type | request | stresses |
|-----|------|---------|----------|
| F1 | greenfield, well-specified | "Daily plan endpoint: ordered plan from open tasks" | best case |
| F2 | brownfield modify | "Add priority + due-date; plan respects both" | existing code + regression risk |
| F3 | multi-issue + dependency | "Backlog aging: aging score + stale-tasks endpoint + decay" | slicing / conflict graph / ordering |
| F4 | deliberately vague | "Make mornings easier to start" | whether grill/PRD earns its cost |
| F5 | trivial fix | "Return created_at in the task list" | whether the full pipeline is overkill |

**Mandatory coverage** (enforced by the auto-reviewer policy): one deliberate
request-changes cycle (assigned to **F2 / PRD gate**), substantive open-question
answers, rescue ≥1 escalation, approve some retro proposals + reject ≥1.

## C1 baseline
Before the pipeline, build **F5 (trivial)** the plain way — one ordinary
`claude -p` session, no pipeline — and record wall-clock / cost / first-try
success as the yardstick (`METRICS.md`). Head-to-head vs. the pipeline's F5.

## Guardrails (the harness enforces these; nothing else does)
- **Per run:** cheapest viable worker model = **haiku-4-5**, `max_turns ≤ 30`,
  `max_cost_usd ≤ 1.50`, `timeout_min` bounded. `can_afford_run` refuses to
  *start* a run that could breach the ceiling.
- **Global:** cost ceiling **$60**, wall-clock ceiling **4h**. Warn at 70% of
  either; **auto-stop at 100%** and write a partial report.
- **Worker isolation:** `permission_mode = acceptEdits` — the strictest mode that
  still lets a headless worker edit. We never pass the permissive
  `auto` / `bypassPermissions`. (Report recommends a container; none here = a
  documented Phase-3 gap.)
- Cost ledger appended per run as it completes (`cost-ledger.md`), tagged
  `real` vs `mock`.

## Models
- `model_worker = claude-haiku-4-5` (cheapest viable; weaker output is itself a
  finding).
- `model_planner = claude-haiku-4-5` (cost-bounded; trade-off documented).
- `model_evaluator / model_auditor = claude-haiku-4-5` (Foreman default).
- Auto-reviewer LLM judge = haiku, read-only (`--permission-mode plan`), capped.

## Execution staging (de-risks real spend; honest real-vs-mock split)
1. **Harness build + unit tests** — pure logic (guardrails, escalation parser,
   reviewer policy) TDD'd. ✅
2. **Mock dry-run** (`demo=True`, $0) — drive the whole pipeline through every
   gate incl. the open-question cycle + fail-first→retry + evaluator/audit/e2e.
   Validates the conductor; surfaces real TUI/machinery findings. ✅
3. **Real run** — `demo=False` against `~/foreman-dogfood/dayplan`, real haiku
   workers, real LLM judge. Ramp parallelism (F1 at `max_parallel=1`). Stop at
   ceilings with a partial report.
4. **Flywheel (C3)** — `foreman retro` over real runs → auto-review proposals
   (approve sound, reject ≥1) → `foreman bench` (mocked, free) → payoff re-run.
5. **Reports** — ITERATION_REPORT / METRICS / AUTOREVIEW_LOG / FLYWHEEL, honestly
   separating real machinery/agent findings from the human-DX questions this
   unattended run cannot answer.

## Scope boundary
Phase 2 is the ceiling. No Phase-3 machinery (training-data exporter, sandboxing,
CLI contract probes, chaos suite). Steps that would have needed them are noted as
Phase-3 dependencies and skipped.
