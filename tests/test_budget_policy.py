"""runner.should_extend — the one predicate for 'resume with more turns vs escalate'.

Cost/timeout/stuck kills never extend; only a turn cut-off (or an explicit
worker request) does, and only when auto-extend is on, a session exists, and the
extension cap is not yet reached.
"""

from foreman.runner import (
    should_extend,
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
