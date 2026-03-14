"""Domain model: enums and dataclasses for the orchestration state machine.

These are plain data carriers. Persistence lives in ``state.py``; the only state
of record is the files on disk, and every object here is reconstructable from
them (R4).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class DocStatus(str, Enum):
    """Lifecycle of a gated document (plan / adr / prd) — R3, §5."""

    DRAFTING = "drafting"
    IN_REVIEW = "in_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"


class IssueStatus(str, Enum):
    """Lifecycle of an implementation issue — §5, extended for Phase 2 (P2.3 WS2)."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    TESTS_FAILING = "tests_failing"
    AWAITING_EVALUATION = "awaiting_evaluation"  # passed Foreman's gate; evaluator pending
    NEEDS_HUMAN = "needs_human"
    DONE = "done"
    MERGED = "merged"


# Schema version of the on-disk ``.foreman/`` tree. Phase-1 trees carry no marker
# and are treated as v1; ``FileStore`` migrates them additively to v2 (P2.2).
SCHEMA_VERSION = 2

# Issue kinds (P2.3 WS4): ordinary feature work vs. janitor passes.
ISSUE_KIND_FEATURE = "feature"
ISSUE_KIND_JANITOR = "janitor"


class Phase(str, Enum):
    """High-level phase of a feature through the pipeline (§6, §7)."""

    REQUEST = "request"          # request.md captured, nothing spawned yet
    PLANNING = "planning"        # planner agent running
    PLAN_REVIEW = "plan_review"  # plan.md awaiting human gate
    GRILLING = "grilling"        # grill agent producing adr.md + prd.md
    DOC_REVIEW = "doc_review"    # adr.md / prd.md awaiting human gate
    SLICING = "slicing"          # slicer agent producing issues
    QUEUE_REVIEW = "queue_review"  # final gate: confirm the issue queue
    BUILDING = "building"        # autonomous build loop running
    E2E = "e2e"                  # e2e agent running
    DONE = "done"


# Which doc kinds are gated, in order.
DOC_KINDS = ("plan", "adr", "prd")


@dataclass
class Budget:
    """Per-run guardrail budget (R5, §5, §9)."""

    max_turns: int = 80
    max_cost_usd: float = 5.00
    timeout_min: int = 45

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "max_cost_usd": self.max_cost_usd,
            "timeout_min": self.timeout_min,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "Budget":
        d = d or {}
        return cls(
            max_turns=int(d.get("max_turns", 80)),
            max_cost_usd=float(d.get("max_cost_usd", 5.00)),
            timeout_min=int(d.get("timeout_min", 45)),
        )


@dataclass
class Approval:
    """An approval record for a gated document (R3)."""

    reviewer: str
    timestamp: str          # ISO-8601 UTC
    body_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer": self.reviewer,
            "timestamp": self.timestamp,
            "body_sha256": self.body_sha256,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> Optional["Approval"]:
        if not d:
            return None
        return cls(
            reviewer=str(d.get("reviewer", "")),
            timestamp=str(d.get("timestamp", "")),
            body_sha256=str(d.get("body_sha256", "")),
        )


@dataclass
class GatedDoc:
    """A versioned, gated document (plan / adr / prd)."""

    kind: str               # one of DOC_KINDS
    version: int
    status: DocStatus
    body: str
    approval: Optional[Approval] = None

    @property
    def open_questions(self) -> list[str]:
        """Extract the '## Open questions for reviewer' bullets from the body.

        The grill skill emits unanswered questions under this heading; the gate
        is only clearable at zero open questions (§4.1, §12).
        """
        return _extract_open_questions(self.body)

    @property
    def has_open_questions(self) -> bool:
        return len(self.open_questions) > 0


@dataclass
class Issue:
    """An implementation issue (§5, extended for Phase 2 — P2.2).

    Phase-2 fields: ``acceptance_check`` (path to a runnable check or a command —
    required for new issues to enter the queue, WS1.1), ``touches`` (declared file
    footprint for conflict-aware scheduling, WS4.1), and ``kind`` (feature|janitor,
    WS4.3). All default empty so Phase-1 issue files still load.
    """

    id: str
    title: str
    status: IssueStatus = IssueStatus.QUEUED
    depends_on: list[str] = field(default_factory=list)
    branch: str = ""
    attempts: int = 0
    budget: Budget = field(default_factory=Budget)
    prd_refs: list[str] = field(default_factory=list)
    body: str = ""
    acceptance_check: str = ""
    touches: list[str] = field(default_factory=list)
    kind: str = ISSUE_KIND_FEATURE

    @property
    def is_janitor(self) -> bool:
        return self.kind == ISSUE_KIND_JANITOR

    @property
    def footprint_known(self) -> bool:
        """A declared, non-empty footprint. Unknown ⇒ conflicts-with-all (P2.2)."""
        return bool(self.touches)

    def frontmatter(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "depends_on": list(self.depends_on),
            "branch": self.branch,
            "attempts": self.attempts,
            "budget": self.budget.to_dict(),
            "prd_refs": list(self.prd_refs),
            "acceptance_check": self.acceptance_check,
            "touches": list(self.touches),
            "kind": self.kind,
        }


@dataclass
class IssueVerification:
    """A single issue's entry in a feature's ``verification.json`` (P2.2, WS1.2).

    The Default-FAIL contract: every issue starts ``passes=False``. Only Foreman
    flips it — workers are blocked from writing this file by a worktree hook
    (WS1.3). ``evidence`` lists artifact paths under ``runs/<id>/evidence/``.
    """

    passes: bool = False
    evidence: list[str] = field(default_factory=list)
    verified_at: Optional[str] = None
    verified_by: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes": self.passes,
            "evidence": list(self.evidence),
            "verified_at": self.verified_at,
            "verified_by": self.verified_by,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "IssueVerification":
        d = d or {}
        return cls(
            passes=bool(d.get("passes", False)),
            evidence=list(d.get("evidence", []) or []),
            verified_at=d.get("verified_at"),
            verified_by=d.get("verified_by"),
        )


@dataclass
class Review:
    """A reviewer's comment set on a specific draft version."""

    doc_kind: str
    version: int
    action: str             # "request_changes" | "approve"
    comments: str
    reviewer: str
    timestamp: str


@dataclass
class RunRecord:
    """Metadata about one worker/agent run, persisted under runs/."""

    run_id: str             # "<timestamp>-<label>"
    label: str              # e.g. "ISS-001" or "planner"
    started: str
    finished: Optional[str] = None
    num_turns: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    terminal_reason: str = ""   # completed | killed_turns | killed_cost | killed_timeout | error
    session_id: Optional[str] = None
    prompt_tokens: int = 0      # WS3.4: assembled-prompt size (context-bloat visibility)
    outcome: str = ""           # WS6: outcome taxonomy label

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FeatureState:
    """In-memory snapshot of a feature, rebuilt from disk on every load (R4)."""

    slug: str
    request: str = ""
    phase: Phase = Phase.REQUEST
    docs: dict[str, GatedDoc] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    queue_confirmed: bool = False
    # Foreman-owned structural "done" map, keyed by issue id (P2.2, WS1.2).
    verification: dict[str, "IssueVerification"] = field(default_factory=dict)

    def doc(self, kind: str) -> Optional[GatedDoc]:
        return self.docs.get(kind)

    def issue(self, issue_id: str) -> Optional[Issue]:
        for i in self.issues:
            if i.id == issue_id:
                return i
        return None

    def ready_issues(self) -> list[Issue]:
        """Issues whose dependencies are all done/merged and which are queued."""
        done_ids = {
            i.id for i in self.issues
            if i.status in (IssueStatus.DONE, IssueStatus.MERGED)
        }
        ready = []
        for i in self.issues:
            if i.status != IssueStatus.QUEUED:
                continue
            if all(dep in done_ids for dep in i.depends_on):
                ready.append(i)
        return ready


def _extract_open_questions(body: str) -> list[str]:
    """Pull markdown bullets under the '## Open questions for reviewer' heading.

    Stops at the next heading of the same or higher level. Bullets that are
    explicitly struck through or marked resolved (``- [x]`` / ``~~...~~``) do not
    count as open.
    """
    lines = body.splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("#"):
            if "open questions for reviewer" in low:
                in_section = True
                continue
            if in_section:
                # A new heading ends the section.
                break
        if not in_section:
            continue
        if stripped.startswith(("- ", "* ", "+ ")):
            content = stripped[2:].strip()
            if content.startswith("[x]") or content.startswith("[X]"):
                continue
            if content.startswith("~~") and content.endswith("~~"):
                continue
            if content:
                out.append(content)
    return out
