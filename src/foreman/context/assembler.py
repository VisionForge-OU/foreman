"""ContextAssembler — the one place worker prompts are built (WS3.4).

Every worker prompt is assembled here from a fixed set of sections, each with an
explicit token budget. Over-budget sections are truncated (with a visible marker)
so a long PRD, conventions doc, or failure log can never silently blow up the
context. The assembler reports a per-section token breakdown that Foreman logs per
run and shows in the TUI, making context bloat visible.

Token counts are estimated (≈4 chars/token) — we deliberately avoid a tokenizer
dependency; the estimate is for budgeting/visibility, not billing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..models import Issue
from ..skill_invocation import HEADLESS_PREAMBLE, use_skill

# Default per-section token budgets. Tuned to keep total worker context lean
# (WS3.4 / context rot) while leaving room for the issue + the failure report.
DEFAULT_BUDGETS: dict[str, int] = {
    "preamble": 250,
    "instructions": 400,
    "issue": 1400,
    "acceptance": 120,
    "prd": 1600,
    "conventions": 900,
    "feature_state": 700,
    "progress": 700,
    "failure_report": 1400,
    "reviewer_answer": 500,
}


def estimate_tokens(text: str) -> int:
    return (len(text or "") + 3) // 4


def _truncate(text: str, budget_tokens: int) -> str:
    max_chars = budget_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n…[truncated to fit context budget]"


@dataclass
class AssembledPrompt:
    text: str
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return sum(self.breakdown.values())


class ContextAssembler:
    def __init__(self, budgets: Optional[dict[str, int]] = None):
        self.budgets = {**DEFAULT_BUDGETS, **(budgets or {})}

    def worker_prompt(
        self,
        issue: Issue,
        commands: dict[str, Optional[str]],
        *,
        evidence_dir: Optional[Path] = None,
        prd_sections: str = "",
        conventions: str = "",
        feature_state: str = "",
        progress: str = "",
        failure_report: str = "",
        reviewer_answer: str = "",
    ) -> AssembledPrompt:
        cmd_lines = "\n".join(
            f"  {name}: {cmd or '(not configured — skip)'}" for name, cmd in commands.items()
        )
        instructions = (
            f"{use_skill('foreman-tdd')}\n\n"
            "Implement EXACTLY this one issue as a single vertical slice with strict "
            "red-green-refactor. Run `init.sh` first if present. Run tests via the "
            "`foreman-test` wrapper (never the raw runner; `--fast` for inner loops).\n"
            f"Project commands:\n{cmd_lines}\n"
        )
        if issue.acceptance_check:
            instructions += (
                f"Acceptance check (Foreman re-runs it — make it pass): "
                f"{issue.acceptance_check}\n"
            )
        if evidence_dir is not None:
            progress_path = Path(evidence_dir).parent / "progress.md"
            instructions += (
                f"COMPLETION CONTRACT: save evidence artifacts into: {evidence_dir}\n"
                "and list them in the FOREMAN-SUMMARY `evidence` array; an unbacked "
                "claim is rejected. MANDATORY HANDOFF: before you stop, write your "
                f"handoff (what was done / what remains / dead ends tried / next step) "
                f"to: {progress_path}\nYou may NOT write verification.json or any issue "
                "file (a hook blocks it).\n"
            )

        # (section name, raw text, optional header shown before the body)
        raw_sections: list[tuple[str, str, str]] = [
            ("preamble", HEADLESS_PREAMBLE, ""),
            ("instructions", instructions, ""),
            ("issue", f"--- ISSUE {issue.id}: {issue.title} ---\n{issue.body}", ""),
            ("acceptance", issue.acceptance_check, "--- ACCEPTANCE CHECK ---"),
            ("prd", prd_sections, "--- REFERENCED PRD SECTIONS ---"),
            ("conventions", conventions, "--- REPO CONVENTIONS (digest) ---"),
            ("feature_state", feature_state, "--- FEATURE STATE (from the initializer) ---"),
            ("progress", progress, "--- HANDOFF: progress.md from the prior session ---"),
            ("failure_report", failure_report,
             "--- PRIOR ATTEMPT FAILED — distilled failure report ---"),
            ("reviewer_answer", reviewer_answer,
             "--- HUMAN REVIEWER ANSWER to your earlier escalation ---"),
        ]

        parts: list[str] = []
        breakdown: dict[str, int] = {}
        for name, text, header in raw_sections:
            text = (text or "").strip()
            if not text:
                continue
            text = _truncate(text, self.budgets.get(name, 800))
            block = f"{header}\n{text}" if header else text
            parts.append(block)
            breakdown[name] = estimate_tokens(block)
        return AssembledPrompt(text="\n\n".join(parts), breakdown=breakdown)
