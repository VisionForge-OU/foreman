"""``foreman retro`` — cluster recurring failures and propose gated patches (WS6).

The retro flywheel reads a feature's run records, deterministically clusters the
recurring failure patterns (no model needed — clustering is pure), and asks an
analysis agent (``kind="retro"``) to PROPOSE concrete patches to the vendored
``foreman-*`` skills, the evaluator rubric, or the worker prompt templates.

Every proposal is rendered as a markdown review document and pushed through the
**same hash-sealed human-review gate as a PRD** (``state.write_doc`` /
``state.approve_doc``): a skill never self-modifies without an approved, sealed
draft. And — WS6's hard rule — *a proposal cannot land without an attached bench
report* (:func:`is_landable`). On approval the skill version is bumped and the
change is appended to ``SKILL_CHANGELOG.md``.

The agent spawn itself is scheduler wiring; this module owns the prompt, the
proposal parser, the review-doc renderer, the changelog/apply helpers, and the
landability rule.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import metrics as _metrics

# The fenced-JSON schema tag an analysis agent must emit (mirrors FOREMAN-SUMMARY).
PROPOSAL_SCHEMA = "foreman-retro/v1"

# Valid patch targets.
TARGET_SKILL_PREFIX = "skill:"
TARGET_RUBRIC = "rubric"
TARGET_PROMPT_PREFIX = "prompt:"


# --------------------------------------------------------------------------- #
# Failure clustering (deterministic — no model)
# --------------------------------------------------------------------------- #
@dataclass
class FailureCluster:
    """A group of runs sharing a normalized failure signature."""

    pattern: str
    count: int
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"pattern": self.pattern, "count": self.count, "examples": list(self.examples)}


def _signature(outcome: str) -> Optional[str]:
    """A normalized, deterministic failure signature for an outcome label.

    Returns ``None`` for a non-failure (success / legacy) outcome so successes
    do not form clusters. Escalations/rejections group by the leading phrase of
    their reason; evaluator bounces and regressions form their own buckets.
    """
    stem = _metrics.base_label(outcome)
    # Non-failure terminals never cluster: successes, legacy/unlabelled, a clean
    # completion, and an operator-initiated kill (a deliberate human action).
    if stem in _metrics.SUCCESS_LABELS or stem in (
        _metrics.LEGACY, _metrics.COMPLETED, _metrics.KILLED_USER
    ):
        return None
    # Kill reasons (killed_turns/cost/timeout/stuck, error) are first-class failure
    # signatures — the dominant dogfood failure (killed_turns) clusters here via the
    # bare-stem catch-all below, so the flywheel can finally see it.
    if stem == _metrics.EVALUATOR_BOUNCE:
        return "evaluator_bounce"
    if stem in (_metrics.ESCALATED, _metrics.HUMAN_REJECTED):
        reason = _metrics.label_param(outcome).lower().strip()
        if not reason:
            return f"{stem}:(unspecified)"
        # Group by the leading phrase: first few significant words, regression-aware.
        if "regress" in reason or "ratchet" in reason:
            return f"{stem}:regression"
        if "budget" in reason or "cost" in reason:
            return f"{stem}:budget"
        if "timeout" in reason or "timed out" in reason:
            return f"{stem}:timeout"
        if "progress.md" in reason or "handoff" in reason:
            return f"{stem}:handoff"
        lead = " ".join(re.findall(r"[a-z0-9]+", reason)[:3])
        return f"{stem}:{lead or 'other'}"
    return stem


def cluster_failures(records: list[dict[str, Any]]) -> list[FailureCluster]:
    """Deterministically cluster failing runs by normalized signature.

    Output is sorted by descending count then signature so it is stable and
    testable. Successes and legacy runs are ignored.
    """
    buckets: dict[str, list[str]] = {}
    for raw in records or []:
        m = _metrics.from_record(raw)
        sig = _signature(m.outcome)
        if sig is None:
            continue
        example = m.issue_id or m.run_id or m.label or "(run)"
        buckets.setdefault(sig, []).append(example)
    clusters = [
        FailureCluster(pattern=sig, count=len(examples), examples=sorted(set(examples))[:5])
        for sig, examples in buckets.items()
    ]
    clusters.sort(key=lambda c: (-c.count, c.pattern))
    return clusters


# --------------------------------------------------------------------------- #
# Patch proposals
# --------------------------------------------------------------------------- #
@dataclass
class PatchProposal:
    """A concrete, reviewable patch an analysis agent proposes."""

    target: str             # "skill:<name>" | "rubric" | "prompt:<x>"
    title: str
    rationale: str
    diff: str
    version_bump: int = 1

    @property
    def is_skill(self) -> bool:
        return self.target.startswith(TARGET_SKILL_PREFIX)

    @property
    def skill_name(self) -> str:
        return self.target[len(TARGET_SKILL_PREFIX):] if self.is_skill else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "title": self.title,
            "rationale": self.rationale,
            "diff": self.diff,
            "version_bump": self.version_bump,
        }


# --------------------------------------------------------------------------- #
# Deterministic kill-rate proposals — the flywheel must never miss the dominant
# failure just because it is a turn/cost/timeout kill rather than an escalation.
# --------------------------------------------------------------------------- #
# A kill cluster at/above this share of all runs is itself a finding: the flywheel
# drafts the corresponding fix directly, without waiting on the analysis agent.
KILL_RATE_PROPOSAL_THRESHOLD = 0.20
# Below this absolute count a kill is treated as noise, not a recurring pattern.
KILL_RATE_MIN_COUNT = 2

# One concrete, reviewable proposal per recurring kill reason. The {count}/{total}/
# {pct} fields are filled from the cluster so the rationale cites the real numbers.
_KILL_PROPOSAL_TEMPLATES: dict[str, dict[str, str]] = {
    _metrics.KILLED_TURNS: {
        "target": "config:turn_budget",
        "title": "Raise the worker turn budget — recurring turn-budget kills",
        "rationale": (
            "{count} of {total} runs ({pct}%) ended killed_turns — the turn budget is "
            "cutting workers off before they finish, the dominant failure. Raise the "
            "model-aware turn budget (config.run_budget.max_turns / "
            "turns.resolve_budget) and/or the turn-extension ceiling "
            "(config.extension_wall_min, config.max_turn_extensions) so a worker can "
            "finish or hand off cleanly instead of being cut off mid-slice."
        ),
        "diff": (
            "# config / turns.resolve_budget (the worker turn budget)\n"
            "- max_turns: <current per-model budget>\n"
            "+ max_turns: <raise for the worker model, sized to the slice>\n"
            "  # or raise extension_wall_min / max_turn_extensions so the existing\n"
            "  # turn-extension chain runs longer before it gives up."
        ),
    },
    _metrics.KILLED_COST: {
        "target": "config:cost_budget",
        "title": "Re-tune the worker cost budget — recurring cost kills",
        "rationale": (
            "{count} of {total} runs ({pct}%) ended killed_cost. Either raise "
            "config.run_budget.max_cost_usd for these slices or cut the per-turn cost "
            "(smaller assembled prompt / cheaper model for the phase)."
        ),
        "diff": (
            "# config.run_budget.max_cost_usd (or the assembled-prompt size)\n"
            "- max_cost_usd: <current>\n"
            "+ max_cost_usd: <raise>   # or reduce prompt_tokens / model cost"
        ),
    },
    _metrics.KILLED_TIMEOUT: {
        "target": "config:timeout",
        "title": "Raise the wall-clock timeout — recurring timeout kills",
        "rationale": (
            "{count} of {total} runs ({pct}%) ended killed_timeout. Raise "
            "config.run_budget.timeout_min for these slices or split them smaller."
        ),
        "diff": (
            "# config.run_budget.timeout_min\n"
            "- timeout_min: <current>\n"
            "+ timeout_min: <raise>   # or slice the work smaller"
        ),
    },
    _metrics.KILLED_STUCK: {
        "target": "prompt:worker",
        "title": "Address stuck workers — recurring no-progress kills",
        "rationale": (
            "{count} of {total} runs ({pct}%) ended killed_stuck (consecutive turns "
            "with no file/test progress). Sharpen the worker prompt's next-step "
            "guidance, or raise config.stuck_turns if it is firing too early."
        ),
        "diff": (
            "# prompt:worker (progress guidance) or config.stuck_turns\n"
            "- (stuck detection fires after N idle turns)\n"
            "+ (make the next concrete step explicit, or raise stuck_turns)"
        ),
    },
    _metrics.ERROR: {
        "target": "prompt:worker",
        "title": "Investigate agent-run errors — recurring error terminations",
        "rationale": (
            "{count} of {total} runs ({pct}%) ended error (the agent run itself "
            "errored). Inspect the run transcripts for the common failure and harden "
            "the worker prompt / run environment against it."
        ),
        "diff": (
            "# investigate run transcripts; harden prompt:worker or the run env\n"
            "- (agent run errors out)\n"
            "+ (handle the recurring error mode)"
        ),
    },
}


def propose_for_clusters(
    clusters: list[FailureCluster],
    total_runs: int,
    *,
    rate_threshold: float = KILL_RATE_PROPOSAL_THRESHOLD,
    min_count: int = KILL_RATE_MIN_COUNT,
) -> list[PatchProposal]:
    """Deterministic patch proposals for kill clusters above a rate threshold (WS6).

    The analysis agent only ever sees clustered failures; before the flywheel-blindness
    fix a turn/cost/timeout kill never even formed a cluster, so the dominant failure
    was silently ignored ("retro found nothing" while half the runs were killed). This
    makes a high kill rate a first-class proposal trigger: when a kill cluster accounts
    for at least ``rate_threshold`` of all runs (and at least ``min_count`` runs), the
    flywheel drafts the corresponding fix directly — no model needed — so it can never
    miss the turn-budget failure again. Each proposal still goes through the same
    hash-sealed review gate and bench requirement as any other.
    """
    if total_runs <= 0:
        return []
    out: list[PatchProposal] = []
    for c in clusters:
        template = _KILL_PROPOSAL_TEMPLATES.get(c.pattern)
        if template is None:
            continue
        if c.count < min_count or (c.count / total_runs) < rate_threshold:
            continue
        pct = round(100 * c.count / total_runs)
        out.append(
            PatchProposal(
                target=template["target"],
                title=template["title"],
                rationale=template["rationale"].format(
                    count=c.count, total=total_runs, pct=pct
                ),
                diff=template["diff"],
                version_bump=1,
            )
        )
    return out


def build_analysis_prompt(clusters: list[FailureCluster], runs_digest: str) -> str:
    """The prompt the ``kind="retro"`` analysis agent gets.

    It hands the agent the clustered failure patterns + a runs digest and asks
    for concrete, minimal patches to the vendored skills / evaluator rubric /
    worker prompt templates, emitted as a single fenced ``foreman-retro/v1`` JSON
    block this module then parses.
    """
    cluster_lines = (
        "\n".join(
            f"- [{c.count}×] {c.pattern}  (e.g. {', '.join(c.examples)})"
            for c in clusters
        )
        or "- (no recurring failure clusters found)"
    )
    return f"""\
You are the Foreman retro analyst. Your job is to find the ROOT cause behind the
recurring failure patterns below and propose the smallest set of concrete patches
that would prevent them — to a vendored skill, the evaluator rubric, or a worker
prompt template. Do NOT propose code changes to the target repo; propose changes
to Foreman's own process artifacts only.

## Recurring failure clusters (most frequent first)
{cluster_lines}

## Runs digest
{runs_digest}

## Output contract
Emit EXACTLY ONE fenced ```json block tagged as schema "{PROPOSAL_SCHEMA}" with:
{{
  "schema": "{PROPOSAL_SCHEMA}",
  "proposals": [
    {{
      "target": "skill:foreman-tdd" | "rubric" | "prompt:worker",
      "title": "<one line>",
      "rationale": "<why this fixes a clustered pattern; cite the cluster>",
      "diff": "<a unified diff or a precise before/after description>",
      "version_bump": 1
    }}
  ]
}}
Every proposal must map to a cluster above. Prefer one or two high-leverage
changes over many speculative ones. If nothing is worth changing, emit an empty
"proposals" list. Each proposal will go through human review before it can land,
and will require a passing bench report — so be concrete and minimal.
"""


def _extract_json_block(text: str, schema: str = PROPOSAL_SCHEMA) -> Optional[dict[str, Any]]:
    """Pull the first fenced ```json block whose payload carries ``schema``.

    Tolerant: tries every fenced block, then a bare-object fallback; returns
    ``None`` on garbage rather than raising.
    """
    if not text:
        return None
    # Prefer schema-tagged fenced blocks.
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        payload = m.group(1).strip()
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if isinstance(data, dict) and (
            data.get("schema") == schema or "proposals" in data
        ):
            return data
    # Fallback: a bare object somewhere in the text.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except ValueError:
            return None
    return None


def parse_proposals(text: str) -> list[PatchProposal]:
    """Parse a ``foreman-retro/v1`` block into proposals. ``[]`` on garbage."""
    data = _extract_json_block(text)
    if not data:
        return []
    raw_list = data.get("proposals")
    if not isinstance(raw_list, list):
        return []
    out: list[PatchProposal] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target", "") or "").strip()
        if not target:
            continue
        try:
            bump = int(item.get("version_bump", 1))
        except (TypeError, ValueError):
            bump = 1
        out.append(
            PatchProposal(
                target=target,
                title=str(item.get("title", "") or "").strip(),
                rationale=str(item.get("rationale", "") or "").strip(),
                diff=str(item.get("diff", "") or ""),
                version_bump=bump if bump > 0 else 1,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# The hash-sealed review gate (same path as a PRD)
# --------------------------------------------------------------------------- #
def proposal_to_review_doc(proposal: PatchProposal) -> str:
    """Render a proposal as a markdown body for the hash-sealed review gate.

    The body is what ``hashing.body_hash`` seals on approval, so the diff +
    rationale are part of the sealed content: editing them after approval
    auto-invalidates it, exactly like a PRD (R3).
    """
    return f"""\
# Retro patch proposal: {proposal.title or '(untitled)'}

**Target:** `{proposal.target}`
**Version bump:** +{proposal.version_bump}

## Rationale
{proposal.rationale or '(none provided)'}

## Proposed change
```diff
{proposal.diff.rstrip()}
```

<!-- This proposal lands only after human approval AND a passing bench report
     (foreman bench). Approval seals this body; any later edit reverts it to
     in_review (R3), and re-running bench is required. -->
"""


# --------------------------------------------------------------------------- #
# Changelog + apply
# --------------------------------------------------------------------------- #
def append_changelog(
    changelog_path: Path, proposal: PatchProposal, approved_by: str, version: int
) -> None:
    """Append a ``SKILL_CHANGELOG.md`` entry for an approved, landed proposal."""
    changelog_path = Path(changelog_path)
    changelog_path.parent.mkdir(parents=True, exist_ok=True)
    header = "" if changelog_path.exists() else "# SKILL_CHANGELOG\n\n"
    entry = (
        f"## {proposal.target} → v{version}\n\n"
        f"- **{proposal.title or '(untitled)'}** (approved by {approved_by})\n"
        f"- {proposal.rationale or '(no rationale)'}\n\n"
    )
    with changelog_path.open("a", encoding="utf-8") as fh:
        if header:
            fh.write(header)
        fh.write(entry)


def _read_skill_version(skill_md: Path) -> int:
    """Read ``foreman_skill_version`` from a SKILL.md frontmatter (1 if absent)."""
    from .. import frontmatter

    if not skill_md.exists():
        return 1
    doc = frontmatter.parse(skill_md.read_text())
    v = doc.get("foreman_skill_version")
    try:
        return int(v) if v is not None else 1
    except (TypeError, ValueError):
        return 1


def apply_approved(proposal: PatchProposal, skill_dir: Path) -> int:
    """Bump an approved skill's version marker. Returns the NEW version.

    Minimal and safe: it rewrites only the ``foreman_skill_version`` frontmatter
    field in ``SKILL.md`` (the documented landing step). The actual skill-content
    edit is the reviewed diff, applied out of band; the version bump is the
    durable, testable marker the startup version-check keys off. A no-op for a
    missing skill dir (returns 0) rather than raising.
    """
    from .. import frontmatter

    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return 0
    doc = frontmatter.parse(skill_md.read_text())
    current = _read_skill_version(skill_md)
    new_version = current + max(1, proposal.version_bump)
    meta = dict(doc.meta)
    meta["foreman_skill_version"] = new_version
    skill_md.write_text(frontmatter.serialize(meta, doc.body))
    return new_version


# --------------------------------------------------------------------------- #
# The hard rule: no patch lands without a bench report
# --------------------------------------------------------------------------- #
def is_landable(proposal: PatchProposal, bench_report: Any) -> bool:
    """A proposal is landable only with a non-empty attached bench report (WS6).

    ``bench_report`` may be a :class:`~foreman.retro.bench.BenchReport`, a dict,
    a path string, or ``None``. Anything falsy / empty-results blocks landing.
    """
    if proposal is None or bench_report is None:
        return False
    # BenchReport-like: must have at least one result.
    results = getattr(bench_report, "results", None)
    if results is not None:
        return len(results) > 0
    if isinstance(bench_report, dict):
        return bool(bench_report.get("results"))
    if isinstance(bench_report, (str, Path)):
        return bool(str(bench_report).strip())
    return bool(bench_report)
