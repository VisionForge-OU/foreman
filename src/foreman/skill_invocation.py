"""SkillInvocation — the single place that turns a phase + context into a headless
prompt (§6). All prompts reference ONLY the vendored ``foreman-*`` skills, never
the upstream names (R2/§12). If Claude Code changes how skills are invoked from
``-p`` mode, this is the one file to fix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .models import Issue


def use_skill(skill: str) -> str:
    """Standard instruction to invoke a vendored skill by name in headless mode."""
    return (
        f'Use the Skill tool to run the "{skill}" skill (equivalently, the '
        f'/{skill} slash command). It is the vendored Foreman version of this '
        f'skill — follow it exactly and do not use any similarly-named '
        f'user-installed skill.'
    )


HEADLESS_PREAMBLE = (
    "You are running fully headless inside Foreman. There is no interactive user. "
    "Do not ask questions and do not wait for input — produce the required output "
    "files and then stop. Defer anything you genuinely cannot resolve to the "
    "'Open questions for reviewer' section as instructed by the skill. "
    "You run under a bounded turn budget — work efficiently and stop cleanly rather "
    "than sprawling; if you are cut off before finishing, Foreman may resume you to "
    "continue, so leave your output files in the best state you can before stopping."
)


class SkillInvocation:
    """Builds the ``-p`` prompt for each pipeline phase."""

    @staticmethod
    def planner(
        request: str, slug: str, plan_path: Path, *,
        prev_body: Optional[str] = None, review_comments: Optional[str] = None,
    ) -> str:
        # The planner uses no vendored skill — it is a deep, high-effort plan.
        parts = [
            HEADLESS_PREAMBLE,
            "",
            "You are a senior staff engineer. Produce a DEEP implementation plan "
            "for the feature request below, grounded in this repository. First "
            "explore the codebase to understand the current architecture, domain "
            "language (CONTEXT.md if present) and prior decisions (docs/adr/). Then "
            "write a thorough plan: goals, approach, the modules/seams involved, "
            "data and interface changes, risks, sequencing, and testing strategy.",
            "",
            f"Write the plan as markdown to this exact path (body only, no YAML "
            f"frontmatter): {plan_path}",
            "",
            f"--- FEATURE REQUEST ---\n{request}",
        ]
        if prev_body:
            parts.append(
                "\n--- YOUR PRIOR PLAN (revise it; keep everything that still applies, "
                "do not drop earlier requirements) ---\n" + prev_body
            )
        if review_comments:
            parts.append(
                "\n--- REVIEWER COMMENTS on the prior plan — you MUST address ALL of "
                "them in this revision ---\n" + review_comments
            )
        if prev_body or review_comments:
            parts.append(
                "\nEnd the plan with a '## Changelog' section: one bullet per revision "
                "saying what changed and which reviewer comment drove it."
            )
        return "\n".join(parts)

    @staticmethod
    def grill(
        slug: str,
        plan_body: str,
        adr_path: Path,
        prd_path: Path,
        *,
        review_comments: Optional[dict[str, str]] = None,
        prev_bodies: Optional[dict[str, str]] = None,
    ) -> str:
        parts = [HEADLESS_PREAMBLE, "", use_skill("foreman-grill-docs"), ""]
        parts.append(
            "Grill the APPROVED plan below against this codebase and its domain "
            "model. Write an ADR draft and a PRD draft (the PRD per the "
            "foreman-to-prd template). Each draft MUST begin with an "
            "'## Open questions for reviewer' section followed by a "
            "'## Decisions made on your behalf' digest (use a `_None ...` line "
            "if there were no non-obvious judgment calls).\n"
            f"Write the ADR draft (body only) to: {adr_path}\n"
            f"Write the PRD draft (body only) to: {prd_path}\n"
        )
        parts.append(f"\n--- APPROVED PLAN ---\n{plan_body}\n")
        if prev_bodies:
            for kind, body in prev_bodies.items():
                parts.append(f"\n--- PREVIOUS {kind.upper()} DRAFT ---\n{body}\n")
        if review_comments:
            parts.append(
                "\n--- REVIEWER COMMENTS (these answer your previous open "
                "questions; consume them, resolve those branches, remove the "
                "answered questions, and append a ## Changelog) ---"
            )
            for kind, comments in review_comments.items():
                if comments:
                    parts.append(f"\nOn the {kind}:\n{comments}\n")
        return "\n".join(parts)

    @staticmethod
    def slicer(slug: str, prd_body: str, issues_dir: Path) -> str:
        return (
            f"{HEADLESS_PREAMBLE}\n\n"
            f"{use_skill('foreman-to-issues')}\n\n"
            "Break the APPROVED PRD below into small, dependency-ordered, "
            "vertically-sliced issues. Write one file per issue into this exact "
            f"directory, named ISS-001.md, ISS-002.md, ...: {issues_dir}\n"
            "Each file must use the exact YAML-frontmatter issue schema from the "
            "skill, with non-empty prd_refs for traceability.\n\n"
            f"--- APPROVED PRD ---\n{prd_body}\n"
        )

    @staticmethod
    def tdd(
        issue: Issue,
        commands: dict[str, Optional[str]],
        *,
        conventions: str = "",
        failing_output: Optional[str] = None,
        reviewer_answer: Optional[str] = None,
        evidence_dir: Optional[Path] = None,
    ) -> str:
        cmd_lines = "\n".join(
            f"  {name}: {cmd or '(not configured — skip)'}"
            for name, cmd in commands.items()
        )
        parts = [
            HEADLESS_PREAMBLE,
            "",
            use_skill("foreman-tdd"),
            "",
            "Implement EXACTLY this one issue as a single vertical slice using "
            "strict red-green-refactor. Run tests with the `foreman-test` wrapper "
            "(on your PATH) — never the raw runner; it keeps output small and "
            "supports `--fast` for inner-loop runs. The project's commands are:\n"
            f"{cmd_lines}\n",
        ]
        if issue.acceptance_check:
            parts.append(
                "This issue's acceptance check (Foreman re-runs it independently — "
                f"make it pass): {issue.acceptance_check}\n"
            )
        if evidence_dir is not None:
            parts.append(
                "COMPLETION CONTRACT: before claiming done you MUST save evidence "
                f"artifacts (the test log, command outputs) into: {evidence_dir}\n"
                "List each saved artifact in the FOREMAN-SUMMARY `evidence` array. "
                "A 'complete' claim with missing or empty evidence is rejected and "
                "counts as a failed attempt. You may NOT write verification.json or "
                "any issue file — Foreman owns those (a hook will block you).\n"
            )
        parts += [
            "End your run with the required FOREMAN-SUMMARY json block (now "
            "including the `evidence` array).",
            "",
            f"--- ISSUE {issue.id}: {issue.title} ---",
            issue.body,
        ]
        if conventions:
            parts.append(f"\n--- REPO CONVENTIONS ---\n{conventions}")
        if failing_output:
            parts.append(
                "\n--- PREVIOUS ATTEMPT FAILED; fix these failures ---\n"
                f"{failing_output}"
            )
        if reviewer_answer:
            parts.append(
                "\n--- HUMAN REVIEWER ANSWER to your earlier escalation ---\n"
                f"{reviewer_answer}"
            )
        return "\n".join(parts)

    @staticmethod
    def e2e(prd_body: str, e2e_command: Optional[str]) -> str:
        return (
            f"{HEADLESS_PREAMBLE}\n\n"
            f"{use_skill('foreman-tdd')}\n\n"
            "Derive end-to-end tests from the 'User Flows' section of the PRD "
            "below, implement them, and make them pass. Use this e2e command for "
            f"verification: {e2e_command or '(not configured)'}\n"
            "End with the required FOREMAN-SUMMARY json block (issue_id: \"e2e\").\n\n"
            f"--- APPROVED PRD ---\n{prd_body}\n"
        )
