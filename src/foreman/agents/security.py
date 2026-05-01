"""The security-review verdict model + prompt + parser (WS7).

The security reviewer is spawned by the scheduler like the evaluator but with ``--agent
foreman-security-review`` (structurally read-only) and a smaller budget. This module
owns the prompt it is given and the JSON verdict it returns. It mirrors
:mod:`foreman.agents.evaluator`; the ``verdict`` field is the decision, with a guardrail
that a ``pass`` listing a high/medium finding is not merge-worthy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..models import Issue

AGENT_NAME = "foreman-security-review"
SCHEMA = "foreman-security/v1"
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

PASS = "pass"
OBJECTIONS = "objections"
UNCERTAIN = "uncertain"

# A 'pass' verdict that nonetheless lists a finding at one of these severities is
# downgraded to not-merge-worthy.
BLOCKING_SEVERITIES = ("high", "medium")


@dataclass
class Finding:
    severity: str = "low"
    category: str = ""
    file: str = ""
    line: Optional[int] = None
    description: str = ""
    recommendation: str = ""


@dataclass
class Verdict:
    issue_id: str = ""
    verdict: str = UNCERTAIN
    findings: list[Finding] = field(default_factory=list)
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_uncertain(self) -> bool:
        return self.verdict == UNCERTAIN

    @property
    def has_blocking_finding(self) -> bool:
        return any(f.severity.lower() in BLOCKING_SEVERITIES for f in self.findings)

    @property
    def is_pass(self) -> bool:
        """Merge-worthy: ``pass`` AND no high/medium finding. Low findings are
        advisory and do not block — the ``verdict`` field is the decision."""
        return self.verdict == PASS and not self.has_blocking_finding

    def feedback(self) -> str:
        """The findings text handed to the next (fresh) builder on a bounce."""
        lines = [f"Security-review verdict: {self.verdict} — {self.summary}".strip()]
        if self.findings:
            lines.append("Findings to fix:")
            for f in self.findings:
                loc = f"{f.file}:{f.line}" if f.line is not None else f.file
                head = f"  • [{f.severity}] {f.category} {loc} — {f.description}".rstrip()
                lines.append(head)
                if f.recommendation:
                    lines.append(f"      fix: {f.recommendation}")
        return "\n".join(lines)


def parse(text: str) -> Optional[Verdict]:
    """Parse the LAST ``foreman-security/v1`` block. None if absent/unparseable."""
    for blob in reversed(_FENCE_RE.findall(text or "")):
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict) or obj.get("schema") != SCHEMA:
            continue
        return _from_dict(obj)
    return None


def _from_dict(obj: dict[str, Any]) -> Verdict:
    findings: list[Finding] = []
    for fd in (obj.get("findings", []) or []):
        fd = fd or {}
        line = fd.get("line")
        try:
            line = int(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        findings.append(Finding(
            severity=str(fd.get("severity", "low")).lower(),
            category=str(fd.get("category", "")), file=str(fd.get("file", "")),
            line=line, description=str(fd.get("description", "")),
            recommendation=str(fd.get("recommendation", "")),
        ))
    verdict = str(obj.get("verdict", UNCERTAIN)).lower()
    if verdict not in (PASS, OBJECTIONS, UNCERTAIN):
        verdict = UNCERTAIN
    return Verdict(
        issue_id=str(obj.get("issue_id", "")),
        verdict=verdict,
        findings=findings,
        summary=str(obj.get("summary", "")),
        raw=obj,
    )


def build_prompt(issue: Issue, *, prd_sections: str, diff: str, worktree: Path) -> str:
    return (
        "You are running headless as the read-only foreman-security-review agent. "
        "Review this one completed issue's committed diff for vulnerabilities it "
        "introduces and emit exactly one foreman-security/v1 JSON block.\n\n"
        "Judge the security of THIS change — start from the DIFF, trace untrusted data "
        "to its sinks, and confirm each finding is real and reachable in the CURRENT "
        "worktree before reporting it. Keep false positives low: report only a "
        "concrete, plausible exploit path (high/medium), not theoretical hardening; "
        "pass on a clean slice.\n\n"
        f"You may read the full worktree at: {worktree}\n\n"
        f"--- ISSUE {issue.id}: {issue.title} ---\n{issue.body}\n\n"
        f"--- REFERENCED PRD SECTIONS ---\n{prd_sections or '(none matched the prd_refs)'}\n\n"
        f"--- DIFF OF THE SLICE ---\n{diff[:12000] or '(empty diff)'}\n"
    )
