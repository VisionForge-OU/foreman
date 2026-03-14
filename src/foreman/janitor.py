"""Specialist janitor passes (WS4.3).

After every N merged issues, Foreman runs a janitor pass: specialised read-write
agents, **one at a time**, each gated by the *exact same* verification pipeline as
a feature worker (full suite + regression ratchet + evaluator). LLM-written code
drifts toward duplication and convention drift, so the janitors counter that:

- ``dedup`` — find and coalesce re-implemented functionality;
- ``conventions`` — structural quality against CONTEXT.md (small refactors only);
- ``docs`` — keep README / CONTEXT.md / the ADR index current.

Janitor work is created as ordinary issues with ``kind: janitor`` (no acceptance
check; an unknown footprint so they run alone) and appears in the TUI like any
issue.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Issue, ISSUE_KIND_JANITOR
from .skill_invocation import HEADLESS_PREAMBLE


@dataclass
class JanitorKind:
    key: str
    title: str
    body: str
    mandate: str  # the focused instruction for the agent


KINDS: dict[str, JanitorKind] = {
    "dedup": JanitorKind(
        key="dedup",
        title="Janitor: coalesce duplicated functionality",
        body=(
            "## Goal\nFind functionality that has been re-implemented in more than one "
            "place and coalesce it behind a single well-named seam, without changing "
            "observable behaviour.\n\n## Acceptance criteria (testable)\n"
            "- [ ] No behaviour change: the full suite still passes and no previously-"
            "passing test now fails.\n- [ ] At least one genuine duplication is removed "
            "(or a clear note that none was found).\n"
        ),
        mandate=(
            "Search the codebase for duplicated or near-duplicated logic (LLM-written "
            "code drifts toward duplication). Coalesce real duplication behind one seam; "
            "keep changes minimal and behaviour-preserving. If you find none, make no "
            "change and say so. Do not rename broadly or restructure beyond the dedup."
        ),
    ),
    "conventions": JanitorKind(
        key="conventions",
        title="Janitor: conventions & structural quality",
        body=(
            "## Goal\nImprove structural quality against the repo's conventions "
            "(CONTEXT.md, neighbouring code) with SMALL, safe refactors only.\n\n"
            "## Acceptance criteria (testable)\n- [ ] The full suite still passes; no "
            "regression.\n- [ ] Changes are small and convention-aligned (naming, seams, "
            "no needless duplication).\n"
        ),
        mandate=(
            "Review the recently-merged code against CONTEXT.md and the surrounding "
            "conventions. Make ONLY small, safe refactors that improve naming, seams, "
            "and convention-fit. No broad rewrites, no behaviour changes."
        ),
    ),
    "docs": JanitorKind(
        key="docs",
        title="Janitor: keep docs current",
        body=(
            "## Goal\nBring README, CONTEXT.md, and the ADR index back in sync with the "
            "code as it now stands.\n\n## Acceptance criteria (testable)\n- [ ] The full "
            "suite still passes (docs changes must not break anything).\n- [ ] README / "
            "CONTEXT.md / ADR index reflect the current behaviour.\n"
        ),
        mandate=(
            "Update README, CONTEXT.md, and the ADR index so they match the code as it "
            "now stands. Do not invent features; document what exists. Keep edits tight."
        ),
    ),
}


def make_issue(kind_key: str, *, issue_id: str, branch: str) -> Issue:
    k = KINDS[kind_key]
    return Issue(
        id=issue_id, title=k.title, kind=ISSUE_KIND_JANITOR,
        branch=branch, depends_on=[], prd_refs=[], acceptance_check="",
        touches=[],  # unknown footprint ⇒ runs alone (it may touch broadly)
        body=k.body,
    )


def build_prompt(
    issue: Issue, kind_key: str, *, evidence_dir, feature_state: str = ""
) -> str:
    k = KINDS[kind_key]
    parts = [
        HEADLESS_PREAMBLE, "",
        f"You are a specialist **{k.key} janitor**. {k.mandate}",
        "",
        "Run `init.sh` first. Run the full `foreman-test` before finishing; Foreman "
        "re-runs it and a regression ratchet — you must not break any passing test. "
        "An independent evaluator will grade your change.",
        f"COMPLETION CONTRACT: save evidence (the test log) into {evidence_dir} and "
        "list it in the FOREMAN-SUMMARY `evidence` array; write your handoff to "
        "progress.md in that run dir before stopping. You may NOT write "
        "verification.json or any issue file.",
        "",
        f"--- JANITOR TASK {issue.id} ---\n{issue.body}",
    ]
    if feature_state:
        parts.append(f"\n--- FEATURE STATE ---\n{feature_state}")
    return "\n".join(parts)
