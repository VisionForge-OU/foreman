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
    if stem in _metrics.SUCCESS_LABELS or stem == _metrics.LEGACY:
        return None
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
