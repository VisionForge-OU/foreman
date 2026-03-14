"""Failure-distiller — turn a failed attempt into a ≤1-page report (WS3.3).

On retry Foreman never resumes the failed session's context. Instead it hands the
*fresh* worker a compact, deterministic distillation of what went wrong: what was
attempted, the exact failing output, and notes/hypotheses already raised — so the
new session starts informed but uncontaminated by the old transcript.

Deterministic by default (no model/tokens): it composes the report from the gate's
precise failing output (already greppable, names regressed tests) plus the prior
worker's summary. Bounded to ~1 page so it can't reintroduce context bloat.
"""

from __future__ import annotations

from typing import Optional

from ..summary import WorkerSummary

MAX_CHARS = 2400  # ~1 page / ~600 tokens


def distill(
    *,
    attempt: int,
    reason: str,
    failing_output: str,
    summary: Optional[WorkerSummary] = None,
) -> str:
    """Compose the fresh-retry failure report. Deterministic and bounded."""
    lines = [f"(distilled from attempt #{attempt})", ""]

    lines.append("## What was attempted")
    if summary and summary.files_touched:
        lines.append("Files touched: " + ", ".join(summary.files_touched[:20]))
    if summary and summary.tests_added:
        lines.append("Tests added: " + ", ".join(summary.tests_added[:20]))
    if not (summary and (summary.files_touched or summary.tests_added)):
        lines.append("(prior summary did not record file/test changes)")

    lines.append("")
    lines.append("## Why it was rejected")
    lines.append(reason or "(no reason recorded)")

    fo = (failing_output or "").strip()
    if fo:
        lines.append("")
        lines.append("## Exact failing output")
        lines.append(fo)

    if summary and summary.open_concerns:
        lines.append("")
        lines.append("## Concerns the prior session raised (treat as hypotheses, re-verify)")
        lines += [f"- {c}" for c in summary.open_concerns[:10]]

    report = "\n".join(lines).strip()
    if len(report) > MAX_CHARS:
        report = report[:MAX_CHARS].rstrip() + "\n…[failure report truncated to ~1 page]"
    return report
