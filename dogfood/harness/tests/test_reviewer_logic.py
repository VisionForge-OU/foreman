"""Pure-logic tests for the synthetic reviewer + escalation parsing."""
from __future__ import annotations

import pytest

from dogfood.harness.autoreviewer import AutoReviewer, StubJudge
from dogfood.harness.state_reader import escalation_open


# ---- escalation_open ---- #
def test_escalation_open_true_when_no_answer():
    text = ("## Escalation @ 2026-06-16T01:00:00Z\n\nturn budget exhausted\n\n"
            "<!-- Reviewer: add your answer below this line, then resume from the TUI. -->\n")
    assert escalation_open(text) is True


def test_escalation_open_false_after_answer():
    text = ("## Escalation @ 2026-06-16T01:00:00Z\n\nbudget\n\n<!-- Reviewer: ... -->\n"
            "## Answer @ 2026-06-16T01:05:00Z\n\nuse in-memory store\n")
    assert escalation_open(text) is False


def test_escalation_open_true_when_reescalated_after_answer():
    text = ("## Escalation @ t1\n\na\n## Answer @ t2\n\nb\n"
            "## Escalation @ t3\n\nnew problem\n<!-- Reviewer: ... -->\n")
    assert escalation_open(text) is True


def test_escalation_open_false_on_empty():
    assert escalation_open("") is False
    assert escalation_open("no headings here") is False


# ---- AutoReviewer policy (StubJudge → free, deterministic) ---- #
async def test_open_questions_force_request_changes_with_answers():
    ar = AutoReviewer(StubJudge())
    d = await ar.review(gate="prd", slug="f1", request="r", body="b", summary="s",
                        open_questions=["Which store?"], structural_problems=[])
    assert d.action == "request_changes"
    assert "answer" in d.comments.lower()


async def test_structural_problems_force_request_changes():
    ar = AutoReviewer(StubJudge())
    d = await ar.review(gate="queue", slug="f1", request="r", body="b", summary="s",
                        open_questions=[], structural_problems=["ISS-002 missing acceptance_check"])
    assert d.action == "request_changes"
    assert "acceptance_check" in d.comments


async def test_mandatory_coverage_forces_one_rc_then_approves():
    ar = AutoReviewer(StubJudge(), force_rc_gates={("f1", "prd")})
    first = await ar.review(gate="prd", slug="f1", request="r", body="b", summary="s",
                            open_questions=[], structural_problems=[])
    assert first.action == "request_changes"
    second = await ar.review(gate="prd", slug="f1", request="r", body="b", summary="s",
                             open_questions=[], structural_problems=[])
    assert second.action == "approve"  # forced cycle consumed


async def test_clean_draft_approves():
    ar = AutoReviewer(StubJudge())
    d = await ar.review(gate="plan", slug="f1", request="r", body="b", summary="s",
                        open_questions=[], structural_problems=[])
    assert d.action == "approve"


async def test_reject_at_least_one_proposal():
    ar = AutoReviewer(StubJudge())
    d1 = await ar.review_proposal(name="p1", detail="d", allow_force_reject=True)
    assert d1.action == "reject"
    d2 = await ar.review_proposal(name="p2", detail="d", allow_force_reject=True)
    assert d2.action == "approve"  # only one forced reject
