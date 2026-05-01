"""MergeGate — the one place the merge VERDICT is decided (deepening 1).

``gate.run_gate`` answers the STRUCTURAL question (evidence + acceptance + suite +
ratchet). This module wraps it with the evaluator stage and the
bounce-vs-escalate policy, returning ONE :class:`GateDecision` that names the
action the scheduler must take: ``MERGE``, ``BOUNCE`` (retry a fresh builder), or
``ESCALATE`` (hand to a human).

The evaluator stays a separate read-only ``--agent`` (DECISIONS §2/WS2): this
module never spawns it — the caller injects ``evaluate``. Likewise the commit that
makes the slice reviewable + mergeable is injected as ``on_structural_pass`` and
awaited the instant the structural gate passes, BEFORE the evaluator diffs the
worktree (otherwise it would diff an uncommitted tree). Side effects are confined
to ``run_gate``'s own subprocess runs plus those two injected callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Optional

from ..agents import evaluator as evaluator_mod
from ..models import Issue
from .gate import GateResult, run_gate


class Action(str, Enum):
    MERGE = "merge"        # structural gate (+ evaluator, if enabled) passed
    BOUNCE = "bounce"      # retry a fresh builder with ``report``
    ESCALATE = "escalate"  # hand to a human with ``reason``


@dataclass
class GateDecision:
    action: Action
    gate: GateResult
    verdict: Optional[evaluator_mod.Verdict] = None
    reason: str = ""          # human message for ESCALATE
    report: str = ""          # distilled failure report for a fresh retry (BOUNCE)
    outcome: str = ""         # metrics hint for ESCALATE: "gate failing" | ...
    is_evaluator_bounce: bool = False


async def decide(
    *,
    issue: Issue,
    worktree: Path,
    commands,
    check_dir: Optional[Path],
    evidence_dir: Path,
    baseline_path: Path,
    summary_evidence,
    env,
    timeout_s: float,
    attempts: int,
    max_retries: int,
    eval_bounces: int,
    evaluator_enabled: bool,
    on_structural_pass: Callable[[], Awaitable[None]],
    distill: Callable[..., str],
    evaluate: Optional[
        Callable[[GateResult], Awaitable[Optional[evaluator_mod.Verdict]]]
    ] = None,
    summary_claims_pass: Optional[bool] = None,
    # WS7: additional read-only gate agents, run in order after the evaluator passes.
    # Each callback returns a verdict exposing ``.is_pass`` / ``.is_uncertain`` /
    # ``.feedback()`` (the same contract as the evaluator's Verdict).
    code_review_enabled: bool = False,
    code_review: Optional[Callable[[GateResult], Awaitable[object]]] = None,
    security_review_enabled: bool = False,
    security_review: Optional[Callable[[GateResult], Awaitable[object]]] = None,
) -> GateDecision:
    """Run the full merge gate and return ONE decision (see module docstring)."""
    gate = await run_gate(
        worktree=worktree, commands=commands, issue=issue, check_dir=check_dir,
        evidence_dir=evidence_dir, baseline_path=baseline_path,
        summary_evidence=summary_evidence, env=env, timeout_s=timeout_s,
    )

    if gate.passed:
        # Commit FIRST: the graders diff the committed worktree and a pass must leave a
        # mergeable commit — this must precede every grader.
        await on_structural_pass()

        verdict: Optional[evaluator_mod.Verdict] = None
        if evaluator_enabled:
            verdict = await evaluate(gate) if evaluate is not None else None
            if verdict is None or verdict.is_uncertain:
                return GateDecision(
                    action=Action.ESCALATE, gate=gate, verdict=verdict,
                    reason=(
                        "evaluator could not decide (uncertain/unparseable verdict) — "
                        "human review needed.\n"
                        + (verdict.feedback() if verdict else "(no parseable verdict)")
                    ),
                    outcome="evaluator uncertain",
                )
            if not verdict.is_pass:
                new_attempts = attempts + 1
                new_eval_bounces = eval_bounces + 1
                report = distill(
                    attempt=new_attempts,
                    reason="the independent evaluator rejected the work",
                    failing_output=verdict.feedback(),
                )
                # Repeated builder-vs-grader disagreement → escalate both sides.
                if new_eval_bounces >= 2 or new_attempts >= max_retries:
                    return GateDecision(
                        action=Action.ESCALATE, gate=gate, verdict=verdict,
                        reason=(
                            f"evaluator objected {new_eval_bounces}x (builder claimed done) — "
                            f"human review needed.\n{verdict.feedback()}"
                        ),
                        report=report, outcome="evaluator disagreement",
                        is_evaluator_bounce=True,
                    )
                return GateDecision(
                    action=Action.BOUNCE, gate=gate, verdict=verdict,
                    report=report, is_evaluator_bounce=True,
                )

        # WS7: additional read-only gate agents (code review, then security review).
        # Each shares the evaluator's pass/objections/uncertain contract and reuses the
        # bounce/escalate policy against the shared retry ceiling (a normal attempt, so
        # `is_evaluator_bounce` stays False). Only the enabled stages run; if the
        # evaluator already bounced/escalated above we never reach here.
        for label, enabled, grade in (
            ("code review", code_review_enabled, code_review),
            ("security review", security_review_enabled, security_review),
        ):
            if not enabled:
                continue
            gv = await grade(gate) if grade is not None else None
            if gv is None or gv.is_uncertain:
                return GateDecision(
                    action=Action.ESCALATE, gate=gate, verdict=verdict,
                    reason=(
                        f"{label} could not decide (uncertain/unparseable verdict) — "
                        "human review needed.\n"
                        + (gv.feedback() if gv else "(no parseable verdict)")
                    ),
                    outcome=f"{label} uncertain",
                )
            if not gv.is_pass:
                new_attempts = attempts + 1
                report = distill(
                    attempt=new_attempts,
                    reason=f"the {label} rejected the work",
                    failing_output=gv.feedback(),
                )
                if new_attempts >= max_retries:
                    return GateDecision(
                        action=Action.ESCALATE, gate=gate, verdict=verdict,
                        reason=(
                            f"{label} still objecting after {new_attempts} attempt(s) "
                            f"(builder claimed done):\n{gv.feedback()}"
                        ),
                        report=report, outcome=f"{label} objection",
                    )
                return GateDecision(
                    action=Action.BOUNCE, gate=gate, verdict=verdict, report=report
                )

        # All enabled graders passed (or none enabled) → merge.
        return GateDecision(action=Action.MERGE, gate=gate, verdict=verdict)

    # Gate failed structurally → bounce a fresh session, or escalate if retries spent.
    new_attempts = attempts + 1
    reason = gate.reason
    if summary_claims_pass is True:
        reason += " (your summary claimed success; Foreman's gate disagrees)"
    report = distill(attempt=new_attempts, reason=reason, failing_output=gate.feedback)
    if new_attempts >= max_retries:
        return GateDecision(
            action=Action.ESCALATE, gate=gate,
            reason=(
                f"gate still failing after {new_attempts} attempt(s) "
                f"({gate.reason}):\n{gate.feedback[:1500]}"
            ),
            report=report, outcome="gate failing",
        )
    return GateDecision(action=Action.BOUNCE, gate=gate, report=report)
