"""Outcome taxonomy + run-record aggregation (Phase-2 WS6).

The scheduler stamps every :class:`~foreman.models.RunRecord` with an ``outcome``
label drawn from the taxonomy below at each terminal point. This module is the
*read* side: it parses those labels out of ``runs/*/usage.json`` and aggregates
them into a :class:`Metrics` panel the TUI renders (success rate, mean
retries/issue, cost/issue, escalation histogram, trends).

No model, no tokens, no I/O beyond reading usage.json — pure, deterministic and
tolerant of missing/garbage fields (a metrics pane must never crash the TUI).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Outcome taxonomy (the authoritative label set, WS6 / DECISIONS §P2.3)
# --------------------------------------------------------------------------- #
SUCCESS_FIRST_TRY = "success_first_try"
SUCCESS_AFTER_RETRY = "success_after_retry"   # rendered as success_after_retry(n)
EVALUATOR_BOUNCE = "evaluator_bounce"
ESCALATED = "escalated"                        # rendered as escalated(<reason>)
HUMAN_REJECTED = "human_rejected"              # rendered as human_rejected(<reason>)
LEGACY = "legacy"                              # pre-WS6 / unlabelled runs

# Labels that count as a delivered issue.
SUCCESS_LABELS = (SUCCESS_FIRST_TRY, SUCCESS_AFTER_RETRY)


def label_success(attempts: int) -> str:
    """First-try success vs. success-after-retry(n).

    ``attempts`` is the issue's attempt count at the moment it landed: ``<=1``
    means it succeeded on the first try; otherwise the retry count is embedded.
    """
    if attempts <= 1:
        return SUCCESS_FIRST_TRY
    return f"{SUCCESS_AFTER_RETRY}({attempts})"


def escalated(reason: str) -> str:
    """An ``escalated(<reason>)`` label (reason newlines/commas flattened)."""
    return f"{ESCALATED}({_clean_reason(reason)})"


def human_rejected(reason: str) -> str:
    """A ``human_rejected(<reason>)`` label."""
    return f"{HUMAN_REJECTED}({_clean_reason(reason)})"


def evaluator_bounce() -> str:
    return EVALUATOR_BOUNCE


def _clean_reason(reason: str) -> str:
    reason = (reason or "").strip().replace("\n", " ")
    # Keep the leading phrase compact so histograms group cleanly.
    return re.sub(r"\s+", " ", reason)[:80]


def base_label(label: str) -> str:
    """The taxonomy stem of a label, stripping any ``(...)`` parameter.

    ``success_after_retry(3)`` -> ``success_after_retry``; ``escalated(budget)``
    -> ``escalated``; an empty/None label -> ``legacy``.
    """
    if not label:
        return LEGACY
    return label.split("(", 1)[0].strip() or LEGACY


def label_param(label: str) -> str:
    """The parameter inside a ``stem(param)`` label, or ``""`` if none."""
    m = re.search(r"\(([^)]*)\)\s*$", label or "")
    return m.group(1).strip() if m else ""


def is_success(label: str) -> bool:
    return base_label(label) in SUCCESS_LABELS


def retries_of(label: str) -> int:
    """Number of attempts encoded in a success label (1 for first-try)."""
    stem = base_label(label)
    if stem == SUCCESS_FIRST_TRY:
        return 1
    if stem == SUCCESS_AFTER_RETRY:
        try:
            return int(label_param(label))
        except (TypeError, ValueError):
            return 2
    return 0


# --------------------------------------------------------------------------- #
# Per-run metric (parsed from one usage.json record)
# --------------------------------------------------------------------------- #
def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _wall_seconds(started: Any, finished: Any) -> float:
    a, b = _parse_iso(started), _parse_iso(finished)
    if a is None or b is None:
        return 0.0
    delta = (b - a).total_seconds()
    return delta if delta > 0 else 0.0


def _issue_id_of(label: str) -> str:
    """Derive the issue id from a run label (e.g. ``ISS-001`` from any label)."""
    m = re.search(r"(ISS-\d+)", label or "")
    return m.group(1) if m else ""


@dataclass
class RunMetric:
    """One run flattened to the fields the flywheel cares about."""

    run_id: str
    label: str           # the run label (e.g. "ISS-001")
    outcome: str         # taxonomy label
    cost_usd: float = 0.0
    num_turns: int = 0
    prompt_tokens: int = 0
    wall_seconds: float = 0.0
    issue_id: str = ""

    @property
    def is_success(self) -> bool:
        return is_success(self.outcome)


def from_record(d: dict[str, Any]) -> RunMetric:
    """Parse one ``usage.json`` dict into a :class:`RunMetric` (tolerant)."""
    d = d or {}
    label = str(d.get("label", "") or "")
    outcome = str(d.get("outcome", "") or "") or LEGACY
    issue_id = str(d.get("issue_id", "") or "") or _issue_id_of(label)
    wall = d.get("wall_seconds")
    if wall is None:
        wall = _wall_seconds(d.get("started"), d.get("finished"))
    return RunMetric(
        run_id=str(d.get("run_id", "") or ""),
        label=label,
        outcome=outcome,
        cost_usd=float(d.get("cost_usd", 0.0) or 0.0),
        num_turns=int(d.get("num_turns", 0) or 0),
        prompt_tokens=int(d.get("prompt_tokens", 0) or 0),
        wall_seconds=float(wall or 0.0),
        issue_id=issue_id,
    )


# --------------------------------------------------------------------------- #
# Aggregated, feature-level metrics
# --------------------------------------------------------------------------- #
@dataclass
class Metrics:
    """Aggregate of a feature's runs (one row of the TUI metrics pane)."""

    n_runs: int = 0
    success_rate: float = 0.0
    mean_retries: float = 0.0
    cost_per_issue: float = 0.0
    total_cost: float = 0.0
    escalation_reasons: dict[str, int] = field(default_factory=dict)
    by_outcome: dict[str, int] = field(default_factory=dict)
    slug: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "n_runs": self.n_runs,
            "success_rate": self.success_rate,
            "mean_retries": self.mean_retries,
            "cost_per_issue": self.cost_per_issue,
            "total_cost": self.total_cost,
            "escalation_reasons": dict(self.escalation_reasons),
            "by_outcome": dict(self.by_outcome),
        }


def aggregate(records: list[dict[str, Any]], *, slug: str = "") -> Metrics:
    """Aggregate raw usage.json dicts into a :class:`Metrics`.

    - ``success_rate`` = distinct successfully-landed issues / distinct issues seen.
    - ``mean_retries`` = mean attempt-count among successful landings.
    - ``cost_per_issue`` = total feature cost / distinct issues (0 if none).
    - ``escalation_reasons`` / ``by_outcome`` are histograms by taxonomy stem.
    """
    metrics = [from_record(r) for r in (records or [])]
    by_outcome: dict[str, int] = {}
    escalation_reasons: dict[str, int] = {}
    total_cost = 0.0
    issues: set[str] = set()
    success_issues: dict[str, int] = {}   # issue_id -> retries of its success label

    for m in metrics:
        stem = base_label(m.outcome)
        by_outcome[stem] = by_outcome.get(stem, 0) + 1
        total_cost += m.cost_usd
        if m.issue_id:
            issues.add(m.issue_id)
        if stem in (ESCALATED, HUMAN_REJECTED):
            reason = label_param(m.outcome) or "(unspecified)"
            escalation_reasons[reason] = escalation_reasons.get(reason, 0) + 1
        if is_success(m.outcome):
            key = m.issue_id or m.run_id
            success_issues[key] = retries_of(m.outcome)
            if m.issue_id:
                issues.add(m.issue_id)

    n_issues = len(issues) if issues else len(success_issues)
    n_success = len(success_issues)
    success_rate = (n_success / n_issues) if n_issues else 0.0
    mean_retries = (
        sum(success_issues.values()) / len(success_issues) if success_issues else 0.0
    )
    cost_per_issue = (total_cost / n_issues) if n_issues else 0.0

    return Metrics(
        n_runs=len(metrics),
        success_rate=success_rate,
        mean_retries=mean_retries,
        cost_per_issue=cost_per_issue,
        total_cost=total_cost,
        escalation_reasons=dict(sorted(escalation_reasons.items())),
        by_outcome=dict(sorted(by_outcome.items())),
        slug=slug,
    )


def load_feature_metrics(store: Any, slug: str) -> Metrics:
    """Aggregate every ``runs/*/usage.json`` for a feature.

    ``store`` is a :class:`~foreman.state.FileStore` (or anything exposing
    ``usage_records(slug)``). Missing/garbage records are skipped by the store.
    """
    return aggregate(store.usage_records(slug), slug=slug)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render(metrics: Metrics) -> str:
    """A compact multi-line text panel for the TUI metrics pane."""
    head = f"Metrics{(' — ' + metrics.slug) if metrics.slug else ''}"
    lines = [
        head,
        f"  runs:           {metrics.n_runs}",
        f"  success rate:   {metrics.success_rate * 100:.0f}%",
        f"  mean retries:   {metrics.mean_retries:.2f} / issue",
        f"  cost / issue:   ${metrics.cost_per_issue:.2f}",
        f"  total cost:     ${metrics.total_cost:.2f}",
    ]
    if metrics.by_outcome:
        lines.append("  outcomes:")
        for label, count in metrics.by_outcome.items():
            lines.append(f"    {label:<24} {count}")
    if metrics.escalation_reasons:
        lines.append("  escalations:")
        for reason, count in metrics.escalation_reasons.items():
            lines.append(f"    {count:>3}× {reason}")
    return "\n".join(lines)


def trend(per_feature: list[Metrics]) -> str:
    """A last-N-features trend summary (success rate + cost/issue over time)."""
    if not per_feature:
        return "Trend: (no features yet)"
    n = len(per_feature)
    lines = [f"Trend (last {n} feature{'s' if n != 1 else ''}):"]
    for m in per_feature:
        name = m.slug or "(feature)"
        arrow = ""
        lines.append(
            f"  {name:<28} {m.success_rate * 100:>3.0f}%  "
            f"${m.cost_per_issue:.2f}/issue  {m.mean_retries:.1f} retries{arrow}"
        )
    first, last = per_feature[0], per_feature[-1]
    d_sr = (last.success_rate - first.success_rate) * 100
    d_cost = last.cost_per_issue - first.cost_per_issue
    lines.append(
        f"  Δ success {d_sr:+.0f}pp   Δ cost/issue ${d_cost:+.2f}"
    )
    return "\n".join(lines)
