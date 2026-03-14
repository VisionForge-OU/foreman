"""The evaluator verdict model + prompt + parser (P2.3 WS2).

The evaluator is spawned by the scheduler like a worker but with ``--agent
foreman-evaluator`` (structurally read-only) and a smaller budget. This module
owns the prompt it is given and the graded JSON verdict it returns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..models import Issue

AGENT_NAME = "foreman-evaluator"
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
RUBRIC = ("functionality", "prd_fidelity", "craft", "test_honesty")

PASS = "pass"
OBJECTIONS = "objections"
UNCERTAIN = "uncertain"


@dataclass
class RubricScore:
    score: int = 0
    justification: str = ""


@dataclass
class Verdict:
    issue_id: str = ""
    verdict: str = UNCERTAIN
    scores: dict[str, RubricScore] = field(default_factory=dict)
    objections: list[str] = field(default_factory=list)
    summary: str = ""
    min_score: int = 3
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def lowest(self) -> int:
        return min((s.score for s in self.scores.values()), default=0)

    @property
    def is_uncertain(self) -> bool:
        return self.verdict == UNCERTAIN

    @property
    def is_pass(self) -> bool:
        """Merge-worthy: explicit pass, no objections, every rubric score >= min."""
        return (
            self.verdict == PASS
            and not self.objections
            and self.lowest >= self.min_score
        )

    def feedback(self) -> str:
        """The objection text handed to the next (fresh) builder on a bounce."""
        lines = [f"Evaluator verdict: {self.verdict} — {self.summary}".strip()]
        for dim in RUBRIC:
            s = self.scores.get(dim)
            if s:
                lines.append(f"- {dim}: {s.score}/5 — {s.justification}")
        if self.objections:
            lines.append("Objections to fix:")
            lines += [f"  • {o}" for o in self.objections]
        return "\n".join(lines)


def parse(text: str, *, min_score: int = 3) -> Optional[Verdict]:
    """Parse the LAST ``foreman-verdict/v1`` block. None if absent/unparseable."""
    for blob in reversed(_FENCE_RE.findall(text or "")):
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict) or obj.get("schema") != "foreman-verdict/v1":
            continue
        return _from_dict(obj, min_score=min_score)
    return None


def _from_dict(obj: dict[str, Any], *, min_score: int) -> Verdict:
    scores: dict[str, RubricScore] = {}
    for dim, sc in (obj.get("scores", {}) or {}).items():
        sc = sc or {}
        try:
            val = int(sc.get("score", 0))
        except (TypeError, ValueError):
            val = 0
        scores[str(dim)] = RubricScore(score=val, justification=str(sc.get("justification", "")))
    verdict = str(obj.get("verdict", UNCERTAIN)).lower()
    if verdict not in (PASS, OBJECTIONS, UNCERTAIN):
        verdict = UNCERTAIN
    return Verdict(
        issue_id=str(obj.get("issue_id", "")),
        verdict=verdict,
        scores=scores,
        objections=list(obj.get("objections", []) or []),
        summary=str(obj.get("summary", "")),
        min_score=min_score,
        raw=obj,
    )


def build_prompt(
    issue: Issue,
    *,
    prd_sections: str,
    diff: str,
    worktree: Path,
    evidence_dir: Path,
    evidence_artifacts: list[str],
) -> str:
    arts = ", ".join(evidence_artifacts) or "(none listed)"
    return (
        "You are running headless as the read-only foreman-evaluator agent. Grade "
        "this one completed issue and emit exactly one foreman-verdict/v1 JSON block.\n\n"
        f"You may read the full worktree at: {worktree}\n"
        f"Evidence the worker saved is under: {evidence_dir} (artifacts: {arts})\n\n"
        f"--- ISSUE {issue.id}: {issue.title} ---\n{issue.body}\n\n"
        f"--- ACCEPTANCE CHECK (already passed Foreman's independent run) ---\n"
        f"{issue.acceptance_check or '(none)'}\n\n"
        f"--- REFERENCED PRD SECTIONS ---\n{prd_sections or '(none matched the prd_refs)'}\n\n"
        f"--- DIFF OF THE SLICE ---\n{diff[:12000] or '(empty diff)'}\n"
    )
