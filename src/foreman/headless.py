"""Headless full-pipeline runner used by ``foreman run`` (CI / non-interactive).

Drives the entire gated pipeline — plan → grill(ADR/PRD) → slice → build → e2e —
without a TUI. Because there is no human at the gates, ``auto_approve`` approves
each document once it has zero open questions, feeding a generic "use your best
judgment" answer back into the grill loop to drain any open questions first.

This deliberately bypasses the human review gate (R3) and is intended for testing
and automation; interactive use should go through the TUI, which preserves the gate.
"""

from __future__ import annotations

from typing import Callable, Optional

from .models import DocStatus, IssueStatus
from .tui.controller import Controller

GENERIC_ANSWER = (
    "Resolve every open question using your best engineering judgment for this "
    "small, well-scoped feature, then proceed. Do not leave any open questions."
)


class HeadlessError(RuntimeError):
    pass


async def run_feature(
    controller: Controller,
    title: str,
    request: str,
    *,
    auto_approve: bool = True,
    grill_retries: int = 3,
    on_log: Callable[[str], None] = print,
):
    """Run a feature from zero to a finished build. Returns (slug, report)."""
    c = controller
    c.log_sink = on_log

    missing = c.missing_required()
    if missing:
        raise HeadlessError(f"required skills missing: {', '.join(missing)} — run `foreman init`")
    if not auto_approve:
        raise HeadlessError("headless run needs --auto-approve (gates cannot be reviewed in a CLI run)")

    slug = c.create_feature(title, request)
    on_log(f"• feature created: {slug}")

    # --- Plan ---
    on_log("• running planner…")
    await c.run_planner(slug)
    plan = c.feature(slug).doc("plan")
    on_log(f"• plan.md produced (v{plan.version}, {len(plan.open_questions)} open questions)")
    _auto_resolve_and_approve(c, slug, "plan", grill_retries=0, on_log=on_log, regrill=False)
    on_log("• plan APPROVED")

    # --- Grill -> ADR + PRD (with open-questions loop) ---
    on_log("• running grill (ADR + PRD)…")
    await c.run_grill(slug)
    for attempt in range(grill_retries + 1):
        st = c.feature(slug)
        adr, prd = st.doc("adr"), st.doc("prd")
        open_qs = (adr.open_questions if adr else []) + (prd.open_questions if prd else [])
        on_log(f"  grill pass {attempt + 1}: {len(open_qs)} open question(s)")
        for q in open_qs:
            on_log(f"    ? {q}")
        if not open_qs:
            break
        if attempt == grill_retries:
            raise HeadlessError(
                f"grill still has {len(open_qs)} open question(s) after "
                f"{grill_retries + 1} passes; a human is needed (use the TUI)"
            )
        if adr and adr.has_open_questions:
            c.request_changes(slug, "adr", GENERIC_ANSWER)
        if prd and prd.has_open_questions:
            c.request_changes(slug, "prd", GENERIC_ANSWER)
        on_log("  answered open questions; re-running grill…")
        await c.run_grill(slug)

    c.approve(slug, "adr")
    c.approve(slug, "prd")
    on_log("• ADR + PRD APPROVED")

    # --- Slice ---
    on_log("• running slicer…")
    await c.run_slicer(slug)
    issues = c.feature(slug).issues
    if not issues:
        raise HeadlessError("slicer produced no issues")
    on_log(f"• {len(issues)} issues: " + ", ".join(f"{i.id}({'→'.join(i.depends_on) or 'no deps'})"
                                                    for i in issues))
    c.confirm_queue(slug)
    on_log("• queue CONFIRMED — final gate passed")

    # --- Build ---
    on_log("• starting autonomous build loop…")
    report = await c.build(slug)
    on_log("")
    on_log(report.render())
    return slug, report


def _auto_resolve_and_approve(c, slug, kind, *, grill_retries, on_log, regrill):
    doc = c.feature(slug).doc(kind)
    if doc is None:
        raise HeadlessError(f"no {kind} document to approve")
    if doc.has_open_questions:
        raise HeadlessError(f"{kind} has open questions but no resolution path")
    c.approve(slug, kind)
