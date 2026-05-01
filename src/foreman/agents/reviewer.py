"""The code-review verdict model + prompt + parser (WS7).

The code reviewer is spawned by the scheduler like the evaluator but with ``--agent
foreman-code-review`` (structurally read-only) and a smaller budget. This module owns
the prompt it is given and the JSON verdict it returns. It mirrors
:mod:`foreman.agents.evaluator`; the verdict's ``verdict`` field is the decision, with
a guardrail that a ``pass`` listing a blocking-severity issue is not merge-worthy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..models import Issue

AGENT_NAME = "foreman-code-review"
SCHEMA = "foreman-codereview/v1"
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

PASS = "pass"
OBJECTIONS = "objections"
UNCERTAIN = "uncertain"

# A 'pass' verdict that nonetheless lists an issue at one of these severities is
# downgraded to not-merge-worthy (mirrors the evaluator's rubric-score guardrail).
BLOCKING_SEVERITIES = ("critical", "important")


@dataclass
class ReviewIssue:
    severity: str = "minor"
    file: str = ""
    line: Optional[int] = None
    what: str = ""
    why: str = ""
    fix: str = ""


@dataclass
class Verdict:
    issue_id: str = ""
    verdict: str = UNCERTAIN
    strengths: list[str] = field(default_factory=list)
    issues: list[ReviewIssue] = field(default_factory=list)
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_uncertain(self) -> bool:
        return self.verdict == UNCERTAIN

    @property
    def has_blocking_issue(self) -> bool:
        return any(i.severity.lower() in BLOCKING_SEVERITIES for i in self.issues)

    @property
    def is_pass(self) -> bool:
        """Merge-worthy: the reviewer returned ``pass`` AND listed no blocking issue.

        Minor issues alongside a ``pass`` are advisory and do not block — the
        ``verdict`` field is the decision. A ``pass`` that still lists a
        critical/important issue is a contradiction and treated as not merge-worthy."""
        return self.verdict == PASS and not self.has_blocking_issue

    def feedback(self) -> str:
        """The findings text handed to the next (fresh) builder on a bounce."""
        lines = [f"Code-review verdict: {self.verdict} — {self.summary}".strip()]
        if self.issues:
            lines.append("Issues to fix:")
            for i in self.issues:
                loc = f"{i.file}:{i.line}" if i.line is not None else i.file
                head = f"  • [{i.severity}] {loc} — {i.what}".rstrip()
                lines.append(head)
                if i.why:
                    lines.append(f"      why: {i.why}")
                if i.fix:
                    lines.append(f"      fix: {i.fix}")
        return "\n".join(lines)


def parse(text: str) -> Optional[Verdict]:
    """Parse the LAST ``foreman-codereview/v1`` block. None if absent/unparseable."""
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
    issues: list[ReviewIssue] = []
    for it in (obj.get("issues", []) or []):
        it = it or {}
        line = it.get("line")
        try:
            line = int(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        issues.append(ReviewIssue(
            severity=str(it.get("severity", "minor")).lower(),
            file=str(it.get("file", "")), line=line,
            what=str(it.get("what", "")), why=str(it.get("why", "")),
            fix=str(it.get("fix", "")),
        ))
    verdict = str(obj.get("verdict", UNCERTAIN)).lower()
    if verdict not in (PASS, OBJECTIONS, UNCERTAIN):
        verdict = UNCERTAIN
    return Verdict(
        issue_id=str(obj.get("issue_id", "")),
        verdict=verdict,
        strengths=[str(s) for s in (obj.get("strengths", []) or [])],
        issues=issues,
        summary=str(obj.get("summary", "")),
        raw=obj,
    )


def build_prompt(issue: Issue, *, prd_sections: str, diff: str, worktree: Path) -> str:
    return (
        "You are running headless as the read-only foreman-code-review agent. Review "
        "this one completed issue's committed diff and emit exactly one "
        "foreman-codereview/v1 JSON block.\n\n"
        "Start from the DIFF, then open the files it touches and confirm their CURRENT "
        "state before objecting — the worker may have already fixed something. "
        "Categorise issues by real severity; reserve a blocking verdict for a concrete "
        "defect (critical/important) and pass on a clean slice noting only minor nits.\n\n"
        f"You may read the full worktree at: {worktree}\n\n"
        f"--- ISSUE {issue.id}: {issue.title} ---\n{issue.body}\n\n"
        f"--- ACCEPTANCE CHECK (already passed Foreman's independent run) ---\n"
        f"{issue.acceptance_check or '(none)'}\n\n"
        f"--- REFERENCED PRD SECTIONS ---\n{prd_sections or '(none matched the prd_refs)'}\n\n"
        f"--- DIFF OF THE SLICE ---\n{diff[:12000] or '(empty diff)'}\n"
    )
