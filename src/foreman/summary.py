"""Parse the machine-readable FOREMAN-SUMMARY block a tdd worker emits (§4.4)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class CommandResult:
    ran: bool = False
    passed: Optional[bool] = None
    output_tail: str = ""


@dataclass
class WorkerSummary:
    issue_id: str = ""
    files_touched: list[str] = field(default_factory=list)
    tests_added: list[str] = field(default_factory=list)
    commands: dict[str, CommandResult] = field(default_factory=dict)
    open_concerns: list[str] = field(default_factory=list)
    escalate: bool = False
    escalation_question: str = ""
    # Evidence artifacts the worker claims to have saved under runs/<id>/evidence/
    # (WS1.3 completion contract). Foreman validates these exist and are non-empty.
    evidence: list[str] = field(default_factory=list)
    # The worker is making real progress but ran out of turn budget and is asking
    # for N more turns to continue THIS same session (not an escalation). 0 = no ask.
    request_more_turns: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def claims_pass(self) -> bool:
        """The worker's own claim that every command it ran passed."""
        results = [c for c in self.commands.values() if c.ran]
        return bool(results) and all(c.passed for c in results)


def extract(text: str) -> Optional[WorkerSummary]:
    """Find and parse the LAST valid ``foreman-summary/v1`` JSON block in text.

    Tolerant: returns None if there is no parseable summary, rather than raising.
    """
    candidates = _FENCE_RE.findall(text or "")
    for blob in reversed(candidates):
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("schema") != "foreman-summary/v1":
            continue
        return _from_dict(obj)
    return None


def _from_dict(obj: dict[str, Any]) -> WorkerSummary:
    cmds: dict[str, CommandResult] = {}
    for name, c in (obj.get("commands", {}) or {}).items():
        c = c or {}
        cmds[name] = CommandResult(
            ran=bool(c.get("ran", False)),
            passed=c.get("passed"),
            output_tail=str(c.get("output_tail", "")),
        )
    return WorkerSummary(
        issue_id=str(obj.get("issue_id", "")),
        files_touched=list(obj.get("files_touched", []) or []),
        tests_added=list(obj.get("tests_added", []) or []),
        commands=cmds,
        open_concerns=list(obj.get("open_concerns", []) or []),
        escalate=bool(obj.get("escalate", False)),
        escalation_question=str(obj.get("escalation_question", "")),
        evidence=list(obj.get("evidence", []) or []),
        request_more_turns=_safe_nonneg_int(obj.get("request_more_turns", 0)),
        raw=obj,
    )


def _safe_nonneg_int(value: Any) -> int:
    """Tolerant parse of ``request_more_turns`` (null / "" / junk → 0; clamp ≥ 0)."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
