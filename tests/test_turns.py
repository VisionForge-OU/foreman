"""turns.py — model-aware turn-budget policy (issue #1).

Pure functions over (model, phase, configured_budget, overrides). The effective
turn budget is an exact per-model pin if one is configured, otherwise the larger
of the configured budget and the phase-scaled tier floor.
"""

from foreman.config import Config
from foreman.models import Budget
from foreman.turns import (
    TURN_TIERS,
    PHASE_FACTOR,
    classify_model,
    effective_turns,
    resolve_budget,
)


# --------------------------------------------------------------------------- #
# classify_model
# --------------------------------------------------------------------------- #
def test_haiku_is_small():
    assert classify_model("claude-haiku-4-5") == "small"
    assert classify_model("claude-haiku-4-5-20251001") == "small"


def test_frontier_families_are_large():
    assert classify_model("claude-sonnet-4-6") == "large"
    assert classify_model("claude-opus-4-8") == "large"
    assert classify_model("claude-fable-5") == "large"


def test_small_hints_match_anywhere_case_insensitive():
    assert classify_model("Some-MINI-model") == "small"
    assert classify_model("vendor-small-v2") == "small"


def test_unknown_model_defaults_small_for_generosity():
    # Fail safe: an unrecognised id gets MORE turns, not fewer.
    assert classify_model("my-custom-model") == "small"
    assert classify_model("") == "small"


# --------------------------------------------------------------------------- #
# effective_turns — exact pin
# --------------------------------------------------------------------------- #
def test_exact_pin_bypasses_tier_phase_and_floor():
    overrides = {"claude-haiku-4-5": 45}
    # grill would normally scale (×1.5); the pin is honored verbatim.
    assert effective_turns("claude-haiku-4-5", "grill", 80, overrides=overrides) == 45
    # and the same in every phase
    assert effective_turns("claude-haiku-4-5", "worker", 80, overrides=overrides) == 45


# --------------------------------------------------------------------------- #
# effective_turns — floor semantics
# --------------------------------------------------------------------------- #
def test_tier_floor_raises_a_too_small_budget():
    # haiku small floor (60) beats a configured 30.
    assert effective_turns("claude-haiku-4-5", "worker", 30) == 60


def test_larger_configured_budget_is_never_reduced():
    assert effective_turns("claude-haiku-4-5", "worker", 80) == 80
    assert effective_turns("claude-opus-4-8", "worker", 80) == 80


def test_frontier_floor_is_lower_than_small():
    # opus large floor (30); configured 30 -> 30 (unchanged).
    assert effective_turns("claude-opus-4-8", "worker", 30) == 30


# --------------------------------------------------------------------------- #
# effective_turns — phase scaling
# --------------------------------------------------------------------------- #
def test_grill_phase_scales_above_worker():
    # small floor 60 × grill 1.5 = 90, beats configured 30.
    assert effective_turns("claude-haiku-4-5", "grill", 30) == 90


def test_e2e_factor_rounds():
    # 60 × 1.25 = 75
    assert effective_turns("claude-haiku-4-5", "e2e", 30) == 75


def test_unknown_phase_uses_factor_one():
    assert effective_turns("claude-haiku-4-5", "mystery", 30) == 60


# --------------------------------------------------------------------------- #
# effective_turns — config overrides of the built-in tables (merged)
# --------------------------------------------------------------------------- #
def test_tier_override_merges_over_defaults():
    assert effective_turns("claude-haiku-4-5", "worker", 30, tiers={"small": 80}) == 80
    # an unspecified tier still uses the default
    assert effective_turns("claude-opus-4-8", "worker", 10, tiers={"small": 80}) == 30


def test_phase_factor_override_merges_over_defaults():
    assert effective_turns("claude-haiku-4-5", "grill", 30, factors={"grill": 2.0}) == 120


# --------------------------------------------------------------------------- #
# table sanity
# --------------------------------------------------------------------------- #
def test_default_tables_present():
    assert TURN_TIERS["small"] > TURN_TIERS["large"]
    assert PHASE_FACTOR["grill"] > PHASE_FACTOR["worker"]


# --------------------------------------------------------------------------- #
# resolve_budget — Config-driven, preserves the non-turn Budget fields
# --------------------------------------------------------------------------- #
def test_resolve_budget_floors_small_model_worker():
    base = Budget(max_turns=30, max_cost_usd=2.0, timeout_min=20)
    b = resolve_budget(Config(), "claude-haiku-4-5", "worker", base)
    assert b.max_turns == 60
    assert b.max_cost_usd == 2.0   # untouched
    assert b.timeout_min == 20     # untouched


def test_resolve_budget_grill_scales():
    b = resolve_budget(Config(), "claude-haiku-4-5", "grill", Budget(max_turns=30))
    assert b.max_turns == 90


def test_resolve_budget_honors_config_pin():
    cfg = Config(turn_budget_by_model={"claude-haiku-4-5": 45})
    b = resolve_budget(cfg, "claude-haiku-4-5", "grill", Budget(max_turns=30))
    assert b.max_turns == 45


def test_resolve_budget_frontier_unchanged_at_high_base():
    b = resolve_budget(Config(), "claude-opus-4-8", "worker", Budget(max_turns=80))
    assert b.max_turns == 80
