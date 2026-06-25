"""Model-aware turn-budget policy (issue #1).

The single owner of "how many turns should this run get?". Pure functions over
``(model, phase, configured_budget, overrides)`` — no I/O, no state — so the
policy can be unit-tested exhaustively and reasoned about in one screen.

Resolution rule:

1. **Exact pin** — if the model has an entry in ``turn_budget_by_model``
   (``overrides``), that integer is returned verbatim, bypassing tier, phase
   factor, and floor. The operator's precise escape hatch.
2. **Otherwise** — ``max(configured, round(tier_floor × phase_factor))``. The
   tier floor can only raise a too-small configured budget, never reduce a
   deliberately large one.

A 30-turn cap suits a frontier model but starves a small/cheap one; the dogfood
soak test ended 49% of runs ``killed_turns`` for exactly this reason.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Optional

from .models import Budget

# Built-in defaults — operator-overridable via config (turn_tiers / phase_turn_factors).
TURN_TIERS: dict[str, int] = {"small": 60, "large": 30}     # tier -> turn floor
PHASE_FACTOR: dict[str, float] = {                           # multiplier on the tier floor
    "planner": 1.0,
    "grill": 1.5,
    "slicer": 1.5,
    "worker": 1.0,
    "e2e": 1.25,
    "init": 1.0,
    "grader": 1.0,
}

# Substring hints (case-insensitive) used to classify a model id into a tier.
SMALL_HINTS: tuple[str, ...] = ("haiku", "mini", "small", "flash", "lite", "nano")
LARGE_HINTS: tuple[str, ...] = ("sonnet", "opus", "fable")

DEFAULT_PHASE_FACTOR: float = 1.0   # unknown phase
DEFAULT_TIER: str = "small"         # unknown model -> generous (more turns, not fewer)


def classify_model(model: str) -> str:
    """Classify a model id into a turn tier.

    A small-model hint wins first, then a known frontier family; an unrecognised
    id falls back to ``small`` so an unknown model gets MORE turns, not fewer.
    """
    m = (model or "").lower()
    if any(h in m for h in SMALL_HINTS):
        return "small"
    if any(h in m for h in LARGE_HINTS):
        return "large"
    return DEFAULT_TIER


def effective_turns(
    model: str,
    phase: str,
    configured: int,
    *,
    overrides: Optional[Mapping[str, int]] = None,
    tiers: Optional[Mapping[str, int]] = None,
    factors: Optional[Mapping[str, float]] = None,
) -> int:
    """Resolve the effective ``max_turns`` for a run (see module docstring)."""
    overrides = overrides or {}
    if model in overrides:
        return int(overrides[model])  # exact pin — verbatim, every phase

    tier_table = {**TURN_TIERS, **(tiers or {})}
    factor_table = {**PHASE_FACTOR, **(factors or {})}
    floor = tier_table[classify_model(model)]
    factor = factor_table.get(phase, DEFAULT_PHASE_FACTOR)
    scaled = round(floor * factor)
    return max(int(configured), scaled)


def resolve_budget(config, model: str, phase: str, base: Budget) -> Budget:
    """A copy of ``base`` with ``max_turns`` resolved for this model + phase.

    The single wiring helper called at every ``RunSpec`` assembly site. Only
    ``max_turns`` changes; ``max_cost_usd`` / ``timeout_min`` are preserved.
    """
    return replace(
        base,
        max_turns=effective_turns(
            model, phase, base.max_turns,
            overrides=config.turn_budget_by_model,
            tiers=config.turn_tiers,
            factors=config.phase_turn_factors,
        ),
    )
