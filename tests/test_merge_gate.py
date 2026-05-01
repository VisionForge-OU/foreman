"""MergeGate.decide() — the compound verdict as ONE test surface (deepening 1).

run_gate is monkeypatched so these tests exercise the verdict POLICY (gate +
evaluator + bounce/escalate) without a real worktree or test suite.
"""

import types

import pytest

from foreman.models import Issue
from foreman.verification import merge_gate
from foreman.verification.merge_gate import Action


def _gate(passed, *, reason="boom", feedback="details"):
    # decide() only reads .passed/.reason/.feedback from the gate result.
    return types.SimpleNamespace(
        passed=passed, reason=reason, feedback=feedback, evidence_artifacts=["log.txt"]
    )


def _verdict(*, is_pass=False, is_uncertain=False, text="verdict feedback"):
    return types.SimpleNamespace(
        is_pass=is_pass, is_uncertain=is_uncertain, feedback=lambda: text
    )


def _distill(**kw):
    return f"REPORT[{kw['reason']}]"


async def _commit_recorder():
    return None


def _kwargs(gate_result, *, monkeypatch, **over):
    """Common decide() kwargs with run_gate stubbed to return gate_result."""
    async def fake_run_gate(**_):
        return gate_result
    monkeypatch.setattr(merge_gate, "run_gate", fake_run_gate)
    commits = []

    async def on_pass():
        commits.append(1)

    base = dict(
        issue=Issue(id="ISS-001", title="t", body="b"),
        worktree="/wt", commands={}, check_dir=None, evidence_dir="/ev",
        baseline_path="/bl", summary_evidence=[], env=None, timeout_s=1.0,
        attempts=0, max_retries=3, eval_bounces=0, evaluator_enabled=False,
        on_structural_pass=on_pass, distill=_distill,
    )
    base.update(over)
    return base, commits


async def test_gate_fail_bounces_with_report(monkeypatch):
    kw, commits = _kwargs(_gate(False), monkeypatch=monkeypatch)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.BOUNCE
    assert d.report == "REPORT[boom]"
    assert d.is_evaluator_bounce is False
    assert commits == []  # no commit when the structural gate fails


async def test_gate_fail_at_retry_ceiling_escalates(monkeypatch):
    kw, commits = _kwargs(_gate(False), monkeypatch=monkeypatch, attempts=2, max_retries=3)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.ESCALATE
    assert d.outcome == "gate failing"
    assert "gate still failing after 3 attempt(s)" in d.reason
    assert commits == []


async def test_gate_pass_evaluator_disabled_merges_and_commits(monkeypatch):
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch, evaluator_enabled=False)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.MERGE
    assert commits == [1]  # committed before returning MERGE


async def test_gate_pass_verdict_pass_merges(monkeypatch):
    async def ev(_g):
        return _verdict(is_pass=True)
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch,
                          evaluator_enabled=True, evaluate=ev)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.MERGE
    assert commits == [1]


async def test_gate_pass_objections_first_time_bounces(monkeypatch):
    async def ev(_g):
        return _verdict(is_pass=False)
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch,
                          evaluator_enabled=True, evaluate=ev, eval_bounces=0)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.BOUNCE
    assert d.is_evaluator_bounce is True
    assert d.report.startswith("REPORT[")
    assert commits == [1]  # committed before grading


async def test_gate_pass_objections_second_time_escalates(monkeypatch):
    async def ev(_g):
        return _verdict(is_pass=False)
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch,
                          evaluator_enabled=True, evaluate=ev, eval_bounces=1)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.ESCALATE
    assert d.outcome == "evaluator disagreement"
    assert "evaluator objected 2x" in d.reason


async def test_gate_pass_uncertain_verdict_escalates(monkeypatch):
    async def ev(_g):
        return _verdict(is_uncertain=True)
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch,
                          evaluator_enabled=True, evaluate=ev)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.ESCALATE
    assert d.outcome == "evaluator uncertain"


async def test_gate_pass_none_verdict_escalates(monkeypatch):
    async def ev(_g):
        return None
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch,
                          evaluator_enabled=True, evaluate=ev)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.ESCALATE
    assert d.outcome == "evaluator uncertain"
    assert "(no parseable verdict)" in d.reason


# --- WS7: the code-review + security-review gate agents --- #


async def test_code_review_pass_merges(monkeypatch):
    async def cr(_g):
        return _verdict(is_pass=True)
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch, evaluator_enabled=False,
                          code_review_enabled=True, code_review=cr)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.MERGE
    assert commits == [1]


async def test_code_review_objections_bounces(monkeypatch):
    async def cr(_g):
        return _verdict(is_pass=False, text="CR findings")
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch, evaluator_enabled=False,
                          code_review_enabled=True, code_review=cr)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.BOUNCE
    assert d.report == "REPORT[the code review rejected the work]"
    assert d.is_evaluator_bounce is False  # counts as a normal attempt
    assert commits == [1]


async def test_code_review_objections_at_ceiling_escalates(monkeypatch):
    async def cr(_g):
        return _verdict(is_pass=False, text="CR findings")
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch, attempts=2, max_retries=3,
                          evaluator_enabled=False, code_review_enabled=True, code_review=cr)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.ESCALATE
    assert d.outcome == "code review objection"
    assert "code review still objecting after 3 attempt(s)" in d.reason


async def test_code_review_uncertain_escalates(monkeypatch):
    async def cr(_g):
        return _verdict(is_uncertain=True)
    kw, _ = _kwargs(_gate(True), monkeypatch=monkeypatch, evaluator_enabled=False,
                    code_review_enabled=True, code_review=cr)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.ESCALATE
    assert d.outcome == "code review uncertain"


async def test_code_review_none_escalates(monkeypatch):
    async def cr(_g):
        return None
    kw, _ = _kwargs(_gate(True), monkeypatch=monkeypatch, evaluator_enabled=False,
                    code_review_enabled=True, code_review=cr)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.ESCALATE
    assert d.outcome == "code review uncertain"
    assert "(no parseable verdict)" in d.reason


async def test_security_runs_after_code_review_and_bounces(monkeypatch):
    async def cr(_g):
        return _verdict(is_pass=True)
    async def sec(_g):
        return _verdict(is_pass=False, text="SEC findings")
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch, evaluator_enabled=False,
                          code_review_enabled=True, code_review=cr,
                          security_review_enabled=True, security_review=sec)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.BOUNCE
    assert d.report == "REPORT[the security review rejected the work]"


async def test_all_three_graders_pass_merges(monkeypatch):
    async def ok(_g):
        return _verdict(is_pass=True)
    kw, commits = _kwargs(_gate(True), monkeypatch=monkeypatch,
                          evaluator_enabled=True, evaluate=ok,
                          code_review_enabled=True, code_review=ok,
                          security_review_enabled=True, security_review=ok)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.MERGE
    assert commits == [1]


async def test_evaluator_objection_short_circuits_before_code_review(monkeypatch):
    # If the evaluator already objects, the later graders never run.
    ran = []
    async def ev(_g):
        return _verdict(is_pass=False)
    async def cr(_g):
        ran.append("cr")
        return _verdict(is_pass=True)
    kw, _ = _kwargs(_gate(True), monkeypatch=monkeypatch,
                    evaluator_enabled=True, evaluate=ev, eval_bounces=0,
                    code_review_enabled=True, code_review=cr)
    d = await merge_gate.decide(**kw)
    assert d.action is Action.BOUNCE
    assert d.is_evaluator_bounce is True
    assert ran == []  # code review skipped
