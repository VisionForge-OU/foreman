"""The spec-integrity audit model + prompt + parser + amendment builder (P2.3 WS5).

After every issue has merged, a read-only ``foreman-auditor`` agent walks the
approved PRD requirement by requirement and classifies each as
``satisfied | diverged | unimplemented``, mapped to the evidence it could read.
This module owns the prompt it is given, the graded JSON audit it returns, and the
**deterministic** (no model) construction of a PRD amendment draft from any
divergences — a draft that re-enters the hash-sealed review gate (approve ⇒
re-seal; reject ⇒ new fix issues), exactly mirroring ``agents/evaluator.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

AGENT_NAME = "foreman-auditor"
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

SATISFIED = "satisfied"
DIVERGED = "diverged"
UNIMPLEMENTED = "unimplemented"
_STATUSES = (SATISFIED, DIVERGED, UNIMPLEMENTED)

AMENDMENT_HEADING = "## PRD Amendment (auto-drafted)"


@dataclass
class AuditFinding:
    requirement: str
    status: str = UNIMPLEMENTED       # one of _STATUSES
    evidence: str = ""
    note: str = ""


@dataclass
class AuditReport:
    requirements: list[AuditFinding] = field(default_factory=list)
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def divergences(self) -> list[AuditFinding]:
        return [f for f in self.requirements if f.status == DIVERGED]

    @property
    def unimplemented(self) -> list[AuditFinding]:
        return [f for f in self.requirements if f.status == UNIMPLEMENTED]

    @property
    def satisfied(self) -> list[AuditFinding]:
        return [f for f in self.requirements if f.status == SATISFIED]

    @property
    def all_satisfied(self) -> bool:
        """Every derived requirement is satisfied (and there is at least one)."""
        return bool(self.requirements) and all(
            f.status == SATISFIED for f in self.requirements
        )

    @property
    def needs_amendment(self) -> bool:
        """Any divergence ⇒ the PRD must be amended + re-reviewed to re-seal."""
        return bool(self.divergences)


def parse(text: str) -> Optional[AuditReport]:
    """Parse the LAST ``foreman-audit/v1`` block. None if absent/unparseable."""
    for blob in reversed(_FENCE_RE.findall(text or "")):
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict) or obj.get("schema") != "foreman-audit/v1":
            continue
        return _from_dict(obj)
    return None


def report_from_raw(obj: dict[str, Any]) -> Optional[AuditReport]:
    """Rebuild an :class:`AuditReport` from a persisted ``audit.json`` dict.

    The on-disk audit (``runs/<id>/audit.json``) stores the raw ``foreman-audit/v1``
    payload. Returns None for an absent/unparseable payload (no ``requirements``).
    """
    if not isinstance(obj, dict) or not obj.get("requirements"):
        return None
    return _from_dict(obj)


def _from_dict(obj: dict[str, Any]) -> AuditReport:
    findings: list[AuditFinding] = []
    for r in (obj.get("requirements", []) or []):
        r = r or {}
        status = str(r.get("status", UNIMPLEMENTED)).lower()
        if status not in _STATUSES:
            status = UNIMPLEMENTED
        findings.append(
            AuditFinding(
                requirement=str(r.get("requirement", "")).strip(),
                status=status,
                evidence=str(r.get("evidence", "")),
                note=str(r.get("note", "")),
            )
        )
    return AuditReport(
        requirements=findings,
        summary=str(obj.get("summary", "")),
        raw=obj,
    )


def build_prompt(prd_body: str, *, worktree: Path, e2e_summary: str = "") -> str:
    """Instruct the read-only auditor to walk the PRD against the merged worktree."""
    e2e = e2e_summary.strip() or "(no e2e summary available)"
    return (
        "You are running headless as the read-only foreman-auditor agent. Every "
        "issue for this feature has merged. Walk the approved PRD below "
        "requirement by requirement, map each to the evidence you can read in the "
        "integration worktree, and classify each as satisfied / diverged / "
        "unimplemented. Emit exactly one foreman-audit/v1 JSON block.\n\n"
        f"You may read the fully-merged integration worktree at: {worktree}\n\n"
        f"--- E2E SUMMARY ---\n{e2e}\n\n"
        f"--- APPROVED PRD (the product intent to audit against) ---\n"
        f"{prd_body or '(empty PRD)'}\n"
    )


def build_amendment(prd_body: str, report: AuditReport) -> str:
    """Return an amended PRD **body** (deterministic — no model).

    Appends a single ``## PRD Amendment (auto-drafted)`` section documenting each
    divergence (requirement, actual observed behaviour, rationale) while leaving
    the original sections fully intact. A human reviews + re-approves to re-seal
    the body hash (R3). If there are no divergences the body is returned unchanged.
    """
    divs = report.divergences
    if not divs:
        return prd_body
    base = prd_body.rstrip()
    lines: list[str] = [
        "",
        "",
        AMENDMENT_HEADING,
        "",
        "_Auto-drafted by the foreman-auditor after the build merged. Reconcile each "
        "item against the spec, then re-approve to re-seal — or request changes to "
        "spin off fix issues._",
        "",
    ]
    if report.summary.strip():
        lines += [f"Audit summary: {report.summary.strip()}", ""]
    for i, f in enumerate(divs, start=1):
        observed = (f.note or f.evidence or "(observed behaviour not described)").strip()
        ev = f.evidence.strip()
        lines.append(f"{i}. **{f.requirement or '(unnamed requirement)'}**")
        lines.append(f"   - Observed behaviour: {observed}")
        if ev:
            lines.append(f"   - Evidence: {ev}")
        lines.append(
            "   - Rationale: the shipped behaviour diverges from the approved spec; "
            "amend the PRD to match reality or reject to drive a fix."
        )
        lines.append("")
    return base + "\n" + "\n".join(lines).rstrip() + "\n"


def fix_issue_bodies(report: AuditReport) -> list[dict]:
    """For each unimplemented/diverged requirement, a ``{title, body}`` dict suitable
    for creating a new fix issue.

    Used when a human **rejects** an amendment: the gap becomes concrete work
    instead of a spec change. Kept simple and deterministic.
    """
    out: list[dict] = []
    for f in report.unimplemented + report.divergences:
        req = f.requirement.strip() or "spec gap"
        verb = "Implement" if f.status == UNIMPLEMENTED else "Reconcile"
        detail = (f.note or f.evidence or "").strip()
        body_lines = [
            f"## Goal",
            f"{verb} the PRD requirement: {req}",
            "",
            f"## Why (audit finding: {f.status})",
            detail or "The spec-integrity auditor flagged this requirement as a gap.",
        ]
        if f.evidence.strip():
            body_lines += ["", f"## Evidence reference", f.evidence.strip()]
        out.append({
            "title": f"{verb}: {req}"[:120],
            "body": "\n".join(body_lines) + "\n",
        })
    return out
