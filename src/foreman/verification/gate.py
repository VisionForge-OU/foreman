"""The merge gate — the single trust boundary that defines structural "done"
(P2.3 WS1.2/1.3/1.4). Foreman runs this ITSELF after a worker claims completion;
the worker's say-so never decides.

A slice is done only when ALL hold:
1. **evidence** — non-empty artifacts under ``runs/<id>/evidence/`` backing the
   claim (a complete summary with no evidence is a failed attempt — WS1.3);
2. **acceptance check** — the issue's runnable check passes (WS1.1);
3. **full suite** — the configured test/lint/typecheck pass (the §7 boundary);
4. **regression ratchet** — no previously-passing test now fails (WS1.4).

When it fails, ``feedback`` is the precise, greppable failing output handed to the
next attempt; ``reason`` is the one-line bounce summary (names regressed tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import verify as verify_mod
from ..models import Issue
from . import evidence as evidence_mod
from . import ratchet as ratchet_mod
from .checks import AcceptanceCheck


@dataclass
class GateResult:
    passed: bool
    evidence: evidence_mod.EvidenceResult
    verify: verify_mod.VerifyResult
    ratchet: ratchet_mod.RatchetResult
    now: ratchet_mod.TestResults
    acceptance: Optional[verify_mod.CommandOutcome] = None
    reason: str = ""
    feedback: str = ""
    regressed: list[str] = field(default_factory=list)

    @property
    def evidence_artifacts(self) -> list[str]:
        return self.evidence.artifacts


def _effective_commands(commands: dict[str, Optional[str]]) -> dict[str, Optional[str]]:
    """Use the foreman-test wrapper for the test command so Foreman's own run
    yields the authoritative per-test results trailer (precise ratchet)."""
    eff = dict(commands)
    if (commands.get("test") or "").strip():
        eff["test"] = "foreman-test"
    return eff


async def run_gate(
    *,
    worktree: Path,
    commands: dict[str, Optional[str]],
    issue: Issue,
    check_dir: Optional[Path],
    evidence_dir: Path,
    baseline_path: Path,
    summary_evidence: Optional[list[str]] = None,
    env: Optional[dict] = None,
    timeout_s: float = 600.0,
) -> GateResult:
    # 1. Evidence contract (skip for janitor passes, which have no issue-evidence).
    if issue.is_janitor:
        ev = evidence_mod.EvidenceResult(ok=True, reason="janitor (no evidence required)")
    else:
        ev = evidence_mod.validate(evidence_dir, summary_evidence)

    # 2. Acceptance check.
    ac = AcceptanceCheck.for_issue(issue, check_dir)
    acceptance: Optional[verify_mod.CommandOutcome] = None
    if ac.present:
        acceptance = await ac.run(worktree, commands, timeout_s=timeout_s, env=env)

    # 3. Full suite (test via foreman-test for structured results) + lint + typecheck.
    vr = await verify_mod.verify(
        worktree, _effective_commands(commands),
        names=("test", "lint", "typecheck"), timeout_s=timeout_s, env=env,
    )

    # 4. Regression ratchet from the test run's authoritative trailer.
    test_outcome = verify_mod.outcome_by_name(vr, "test")
    now = ratchet_mod.parse_test_output(test_outcome.output_tail if test_outcome else "")
    baseline = ratchet_mod.read_baseline(baseline_path)
    rr = ratchet_mod.check(baseline, now)

    acc_ok = acceptance is None or bool(acceptance.passed)
    passed = ev.ok and acc_ok and vr.passed and rr.ok

    reason, feedback = _explain(ev, acceptance, vr, rr)
    return GateResult(
        passed=passed, evidence=ev, verify=vr, ratchet=rr, now=now,
        acceptance=acceptance, reason=reason, feedback=feedback,
        regressed=list(rr.regressed),
    )


def _explain(ev, acceptance, vr, rr):
    """Priority-ordered bounce reason + detailed feedback for the retry prompt."""
    parts: list[str] = []
    reason = ""
    if not ev.ok:
        reason = reason or "missing completion evidence"
        parts.append(f"[evidence] {ev.report()}")
    if acceptance is not None and not acceptance.passed:
        reason = reason or "acceptance check failed"
        parts.append(f"[acceptance] FAILED `{acceptance.command}`\n{acceptance.output_tail}")
    if not rr.ok:
        reason = reason or f"regression: {', '.join(rr.regressed)}"
        parts.append(rr.report())
    if not vr.passed:
        reason = reason or "test/lint/typecheck failed"
        parts.append(vr.report())
        if vr.failures:
            parts.append(vr.failure_output())
    return reason, ("\n\n".join(parts))[:8000]
