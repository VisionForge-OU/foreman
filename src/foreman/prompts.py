"""Prompt decoration — the one place continuation/extension text is composed.

Per-role *base* prompts live with their agents (``context.assembler`` for the TDD
worker, ``agents.evaluator``, ``audit``, ``janitor``, ``context.initializer``,
``skill_invocation`` for Phase A). This module owns the cross-cutting text the
orchestrator used to inline: the "CONTINUE — resumed your session with more turns"
wrappers (worker, generic agent, Phase-A pipeline) and the distilled prior-attempt
failure-report appendix. Keeping them here means the resume contract is tuned in
one place instead of five.
"""

from __future__ import annotations

_FAILURE_HEADER = "--- PRIOR ATTEMPT FAILED — distilled report ---"


def worker_continuation(ext_turns: int) -> str:
    """Prepended to a resumed TDD/janitor worker prompt after a turn extension.

    Ends with a blank line because the caller concatenates it before the prompt.
    """
    return (
        f"CONTINUE — Foreman granted you ~{ext_turns} more turns and "
        "RESUMED your prior session. Pick up exactly where you left off; "
        "do NOT restart from scratch. Finish the slice, run the gate "
        "commands, save evidence, write your progress.md handoff, and "
        "emit the FOREMAN-SUMMARY when done.\n\n"
    )


def agent_continuation(task: str) -> str:
    """Prepended to a resumed non-worker agent (evaluator / e2e / auditor) prompt.

    ``task`` is the agent-specific tail, e.g.
    ``"grading this slice and emit the required verdict JSON block, then stop. Do not start over."``
    """
    return f"CONTINUE — Foreman resumed your prior session with more turns. Finish {task}"


def pipeline_continuation() -> str:
    """The whole resumed prompt for a Phase-A agent (planner / grill / slicer).

    Phase-A agents emit no FOREMAN-SUMMARY, so on a turn cut-off the resume simply
    asks them to finish writing their required output file(s) and stop.
    """
    return (
        "CONTINUE — Foreman granted you more turns and RESUMED your prior "
        "session. Pick up exactly where you left off, finish writing your "
        "required output file(s) to the path(s) given earlier, then stop."
    )


def with_failure_report(prompt: str, failure_report: str) -> str:
    """Append the distilled prior-attempt failure report to a prompt.

    No-op when ``failure_report`` is empty.
    """
    if not failure_report:
        return prompt
    return f"{prompt}\n\n{_FAILURE_HEADER}\n{failure_report}"
