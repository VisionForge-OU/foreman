"""runner.should_extend — the one predicate for 'resume with more turns vs escalate'.

Cost/timeout/stuck kills never extend; only a turn cut-off (or an explicit
worker request) does, and only when auto-extend is on, a session exists, and the
extension cap is not yet reached.
"""

from foreman.models import RunRecord
from foreman.runner import (
    should_extend,
    run_duration_min,
    KILLED_TURNS,
    KILLED_COST,
    KILLED_TIMEOUT,
    KILLED_STUCK,
    COMPLETED,
)

BASE = dict(has_session=True, extensions=0, max_extensions=3, auto_extend=True)


def test_turn_cutoff_with_budget_extends():
    assert should_extend(KILLED_TURNS, **BASE) is True


def test_cost_kill_never_extends():
    assert should_extend(KILLED_COST, **BASE) is False


def test_timeout_and_stuck_never_extend():
    assert should_extend(KILLED_TIMEOUT, **BASE) is False
    assert should_extend(KILLED_STUCK, **BASE) is False


def test_no_session_never_extends():
    assert should_extend(KILLED_TURNS, **{**BASE, "has_session": False}) is False


def test_at_cap_never_extends():
    assert should_extend(KILLED_TURNS, **{**BASE, "extensions": 3}) is False


def test_auto_extend_off_never_extends():
    assert should_extend(KILLED_TURNS, **{**BASE, "auto_extend": False}) is False


def test_explicit_request_extends_even_on_non_turn_terminal():
    # A worker that asked for more turns but finished/was-cut-by-something-else
    # still gets a bounded resume, as long as session + budget + auto-extend hold.
    assert should_extend(COMPLETED, **BASE, requested_more=True) is True


def test_explicit_request_still_blocked_without_budget():
    assert should_extend(COMPLETED, **{**BASE, "extensions": 3}, requested_more=True) is False
    assert should_extend(COMPLETED, **{**BASE, "has_session": False}, requested_more=True) is False


# --------------------------------------------------------------------------- #
# Wall + cost extension ceiling (issue #1) — the primary stop; count is a backstop.
# --------------------------------------------------------------------------- #
def test_under_all_ceilings_still_extends():
    assert should_extend(
        KILLED_TURNS, **BASE,
        chain_wall_min=10.0, chain_cost_usd=0.5,
        wall_ceiling_min=30.0, cost_ceiling_usd=3.0,
    ) is True


def test_wall_ceiling_stops_extension():
    assert should_extend(
        KILLED_TURNS, **BASE,
        chain_wall_min=30.0, chain_cost_usd=0.5,
        wall_ceiling_min=30.0, cost_ceiling_usd=3.0,
    ) is False


def test_cost_ceiling_stops_extension():
    assert should_extend(
        KILLED_TURNS, **BASE,
        chain_wall_min=5.0, chain_cost_usd=3.0,
        wall_ceiling_min=30.0, cost_ceiling_usd=3.0,
    ) is False


def test_zero_max_extensions_disables_count_backstop():
    # Count backstop off (0): a turn-kill keeps extending on wall/cost alone,
    # even after many prior extensions.
    args = {**BASE, "max_extensions": 0, "extensions": 99}
    assert should_extend(
        KILLED_TURNS, **args,
        chain_wall_min=5.0, chain_cost_usd=0.5,
        wall_ceiling_min=30.0, cost_ceiling_usd=3.0,
    ) is True
    # …but the wall ceiling still bites with the count backstop off.
    assert should_extend(
        KILLED_TURNS, **args,
        chain_wall_min=40.0, chain_cost_usd=0.5,
        wall_ceiling_min=30.0, cost_ceiling_usd=3.0,
    ) is False


def test_ceilings_default_off_preserve_legacy_behavior():
    # No ceilings passed (None) ⇒ wall/cost are not consulted; count rules.
    assert should_extend(KILLED_TURNS, **BASE) is True


# --------------------------------------------------------------------------- #
# run_duration_min — minutes between a run's start/finish ISO timestamps
# --------------------------------------------------------------------------- #
def test_run_duration_min_from_timestamps():
    r = RunRecord(run_id="x", label="l",
                  started="2026-06-20T10:00:00Z", finished="2026-06-20T10:09:00Z")
    assert run_duration_min(r) == 9.0


def test_run_duration_min_missing_finish_is_zero():
    r = RunRecord(run_id="x", label="l", started="2026-06-20T10:00:00Z")
    assert run_duration_min(r) == 0.0
