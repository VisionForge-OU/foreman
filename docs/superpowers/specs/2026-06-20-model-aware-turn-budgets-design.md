# Model-aware turn budgets — design

**Issue:** [#1 — Turn-budget exhaustion is the dominant failure mode](https://github.com/VisionForge-OU/foreman/issues/1)
**Date:** 2026-06-20
**Status:** approved-for-planning

## Problem

In the dogfood soak test **21 of 43 agent runs (49%) ended `killed_turns`**. The
per-run turn budget is a single fixed number (`max_turns`) with **no relationship
to the model running the work**. A 30-turn cap suits a frontier model but starves
a small/cheap model (haiku at `effort=low`), which then retries / extends /
escalates — each killed run still costing $0.17–0.26. This is the root cause
behind F1 (build:stuck), F3 (grill fail), the over-cost of F5, and most of the
wall-clock. No skill-text patch can fix it; only the budget policy can.

Today every run is assembled into a `RunSpec` carrying `model=` and `budget=`
**side by side but independently** (`pipeline._spawn`, `issue_run`, scheduler
sites). The model and the turn budget never inform each other — that is the gap.

## Goals

1. Make the per-run turn budget **model-aware**: a small model gets more turns than
   a frontier model by default, so it stops running out mid-task.
2. **Per-phase scaling**: heavy phases (grill, slicer) get more turns than a single
   TDD slice.
3. **Wall-clock + cost extension ceiling**: when a turn-killed run is resumed, the
   stop decision is governed by cumulative wall-clock and cost across the extension
   chain, not by an arbitrary re-run-from-a-turn-cap count.
4. **Loud `killed_turns`**: surface the kill reason and extension-chain stats in the
   TUI and the build report (display-only; the outcome-taxonomy work is issue #2).

## Non-goals

- The outcome taxonomy / `foreman retro` clustering of kills — that is **issue #2**.
  This issue only makes kills *visible*; it does not stamp outcome labels or change
  retro clustering.
- Triage / fast-path for trivial requests — **issue #5**.
- Any change to the grader rubric or the evaluator's pass/fail logic.

## Design

### 1. New module `src/foreman/turns.py` (pure policy)

The single owner of "how many turns should this run get?" — no I/O, no state, just
functions over `(model, phase, configured_budget, overrides)`. Isolated so it can be
unit-tested exhaustively and reasoned about in one screen.

```python
# Built-in defaults (operator-overridable via config; see §3).
TURN_TIERS = {"small": 60, "large": 30}                 # tier -> turn floor
PHASE_FACTOR = {                                          # multiplier on the tier floor
    "planner": 1.0, "grill": 1.5, "slicer": 1.5,
    "worker": 1.0,  "e2e": 1.25,  "init": 1.0, "grader": 1.0,
}
SMALL_HINTS = ("haiku", "mini", "small", "flash", "lite", "nano")  # substring, case-insensitive
LARGE_HINTS = ("sonnet", "opus", "fable")                # known frontier families
DEFAULT_PHASE_FACTOR = 1.0                                # unknown phase
DEFAULT_TIER = "small"                                    # unknown model -> generous

def classify_model(model: str) -> str:
    """A small-model hint wins first, then a known frontier family; an
    unrecognised id falls back to 'small' (fail safe — give an unknown model MORE
    turns, not fewer)."""

def effective_turns(model, phase, configured, *, overrides, tiers=None, factors=None) -> int:
    """Resolve the effective max_turns for a run."""
```

**Resolution rule** (reconciles the three precedence decisions):

1. **Exact pin.** If `model` ∈ `overrides` (the `turn_budget_by_model` config map),
   return that integer **verbatim** — bypassing tier, phase factor, and floor. This
   is the operator's precise escape hatch; an overridden model gets the same turn
   count in every phase.
2. **Otherwise** — `max(configured, round(tier_floor(model) * phase_factor(phase)))`.
   The tier value is a **floor**: it can only raise a too-small configured budget,
   never reduce a deliberately large one.

Worked examples (default config, base `max_turns = 80`):

| model | phase | tier floor | × factor | floor result | configured | **effective** |
|-------|-------|-----------|----------|--------------|-----------|---------------|
| haiku | worker | 60 | ×1.0 | 60 | 80 | **80** (unchanged) |
| haiku | grill | 60 | ×1.5 | 90 | 80 | **90** (raised) |
| haiku | worker (budget lowered to 30) | 60 | ×1.0 | 60 | 30 | **60** (floored ↑) |
| opus | worker | 30 | ×1.0 | 30 | 80 | **80** (unchanged) |
| haiku (pinned 45) | grill | — | — | — | — | **45** (exact pin) |

> The floor's biggest payoff is **protection when an operator lowers the budget to
> save cost** (exactly what the dogfood harness did at 30) — a small model is then
> still guaranteed ≥60, never the punishing cap that caused 49% `killed_turns`. Even
> at the default base of 80, phase scaling lifts grill/slicer to 90 for small models,
> matching the evidence that grill/slicer burn turns hardest.

### 2. Wiring at the `RunSpec` seams

A thin helper in `turns.py`:

```python
def resolve_budget(config, model, phase, base) -> Budget:
    return replace(base, max_turns=effective_turns(
        model, phase, base.max_turns,
        overrides=config.turn_budget_by_model,
        tiers=config.turn_tiers, factors=config.phase_turn_factors))
```

Called at each spec-assembly site, passing the phase and the model already chosen
there:

| site | phase arg | model |
|------|-----------|-------|
| `pipeline._spawn` | `ctx.kind` (planner/grill/slicer) | `ctx.model` |
| `issue_run` worker spec | `"worker"` | `model_worker` |
| `scheduler` init spec | `"init"` | `model_planner` |
| `scheduler` e2e spec | `"e2e"` | `model_worker` |
| `scheduler` grader specs (evaluator/auditor/code/security) | `"grader"` | respective model |

Policy lives only in `turns.py`; call sites pass `(model, phase)` and receive an
adjusted `Budget`. Grader sites get the floor too (harmless — `max(configured, …)`
can only help), keeping one consistent rule everywhere.

### 3. Config surface (`config.py` + installer template)

New fields on `Config` (all with safe defaults; round-tripped in `to_dict` /
`from_dict`; validated):

```yaml
# Per-model exact turn pins (escape hatch; bypasses tiers/phase scaling/floor).
turn_budget_by_model: {}          # e.g. { claude-haiku-4-5: 80 }

# Optional overrides of the built-in tier floors and phase multipliers
# (merged over the defaults — you only specify what you change).
turn_tiers: {}                    # e.g. { small: 80, large: 40 }
phase_turn_factors: {}            # e.g. { grill: 2.0 }

# Wall-clock + cost ceiling for the turn-extension chain (see §4).
extension_wall_min: 30
extension_cost_usd: 3.00
max_turn_extensions: 6            # backstop only (was 2)
```

Validation: tier floors and pins are positive ints; phase factors are positive
floats; `extension_wall_min` ≥ 0; `extension_cost_usd` > 0; `max_turn_extensions`
≥ 0 (0 ⇒ no count backstop, wall/cost only). The installer YAML documents each.

### 4. Wall-clock + cost extension ceiling

`should_extend()` (`runner.py:45`) is the shared owner of the extend-vs-give-up
decision for all three loops. Extend its signature with cumulative guards:

```python
def should_extend(terminal_reason, *, has_session, extensions, max_extensions,
                  auto_extend, requested_more=False,
                  chain_wall_min=0.0, chain_cost_usd=0.0,
                  wall_ceiling_min=None, cost_ceiling_usd=None) -> bool:
    if not auto_extend or not has_session:
        return False
    if max_extensions and extensions >= max_extensions:      # 0 => no count backstop
        return False
    if wall_ceiling_min is not None and chain_wall_min >= wall_ceiling_min:
        return False
    if cost_ceiling_usd is not None and chain_cost_usd >= cost_ceiling_usd:
        return False
    return requested_more or terminal_reason == KILLED_TURNS
```

Each of the three extension loops (`pipeline._spawn`, `issue_run`, the non-worker
agent loop in `scheduler`) accumulates across the chain:

```python
chain_cost_usd += result.record.cost_usd
chain_wall_min += run_duration_min(result.record)   # finished - started
```

and passes the new args + the config ceilings into `should_extend`. The effect:
a turn-killed run keeps resuming the **same session** with a healthy turn grant
until it completes or the cumulative wall/cost ceiling bites — the count is just a
runaway backstop.

`run_duration_min` is derived from the `RunRecord.started` / `finished` ISO
timestamps already persisted (no new timing plumbing).

### 5. Loud `killed_turns` (display-only)

- **TUI** — `controller.worker_finished` (`tui/controller.py:202`): when
  `terminal_reason != "completed"`, append it with a ⚠ marker plus the
  extension-chain summary:
  `■ finished: tests_failing ⚠ killed_turns · 3 extensions · 18.2 min · $0.41`.
  (Phase-A spawns get the analogous treatment where they log.)
- **Report** — `report.render()` (the object returned by `Conductor.build`): a
  "Turn-killed runs" section listing any run that ended `killed_turns` with its
  chain stats, so an unattended operator sees it without grepping `runs/`.

This is presentation only — it reads `terminal_reason`, which already exists. It
does **not** assign outcome labels or touch retro (issue #2).

## Components & boundaries

| unit | responsibility | depends on |
|------|----------------|-----------|
| `turns.py` | pure turn-budget policy (tiers, phase factors, classify, effective_turns, resolve_budget) | `models.Budget`, `config.Config` (read-only) |
| `runner.should_extend` | extend-vs-stop decision incl. wall/cost guards | — (pure) |
| extension loops (3) | accumulate chain wall/cost, call `should_extend`, build specs via `resolve_budget` | `turns`, `runner` |
| `config.Config` | new fields + validation + round-trip | `models.Budget` |
| `controller` / report | render terminal_reason + chain stats | `RunRecord` |

Each is independently testable; `turns.py` and `should_extend` are pure.

## Testing strategy

- **`tests/test_turns.py`** — `classify_model` for haiku / sonnet / opus / fable /
  mini / unknown; exact-pin precedence (bypasses everything); floor vs configured;
  phase factors incl. unknown phase → 1.0; config overrides of tiers/factors merge
  over defaults; rounding.
- **`tests/test_should_extend.py`** (extend existing) — stops on wall ceiling; stops
  on cost ceiling; stops on count backstop; `max_extensions=0` disables the count
  backstop; continues while all under; non-turn kills never extend; cost/timeout/
  stuck never extend.
- **Wiring tests** — a haiku worker spec receives the floored/scaled `max_turns`; a
  grill spec gets ×1.5; a frontier model with a high configured budget is unchanged;
  a pinned model gets the exact value in every phase.
- **Config tests** — new fields round-trip through `to_dict`/`from_dict`; invalid
  values raise `ConfigError`; installer template parses.
- **TUI/report tests** — the killed_turns callout renders with chain stats; a clean
  run shows no callout.

## Rollout / compatibility

- All new config fields default to empty/identity, so **existing `.foreman/config.yaml`
  files behave exactly as before** except for: (a) grill/slicer on a small model rise
  to 90 turns at the default base, (b) extensions now also stop on wall/cost, (c)
  `max_turn_extensions` default 2 → 6 (only affects installs that don't set it).
- No on-disk schema change; `RunRecord` already carries the fields the loud reporting
  reads.
