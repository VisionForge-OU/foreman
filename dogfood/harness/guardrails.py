"""Campaign guardrails — the safety net the goal says *we* must enforce, since
nothing else watches an unattended run.

Two hard ceilings: cumulative cost (USD) and wall-clock (seconds). At
``warn_fraction`` of either, we log a warning; at 100% we auto-stop and write a
partial report. Plus a per-run cost cap so a single worker can't blow the budget
and a pre-flight ``can_afford_run`` so we never *start* a run that could exceed
the ceiling.

Cost is the sum of two pools:
  * Foreman's own worker/agent spend, read straight off disk from every
    ``runs/*/usage.json`` in the scratch target repo (authoritative, survives
    restarts).
  * Harness-side spend (LLM-judge calls, the C1 plain baseline, the probe) that
    Foreman never sees — tracked by the conductor and passed in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GuardrailStatus:
    spent_usd: float
    remaining_usd: float
    elapsed_s: float
    frac_cost: float
    frac_time: float
    level: str          # "ok" | "warn" | "stop"
    reason: str

    @property
    def should_stop(self) -> bool:
        return self.level == "stop"


@dataclass
class Guardrails:
    cost_ceiling_usd: float = 60.0
    wall_clock_seconds: float = 4 * 3600
    per_run_max_turns: int = 30
    per_run_max_cost_usd: float = 1.50
    warn_fraction: float = 0.70

    def status(self, *, foreman_spend_usd: float, harness_spend_usd: float,
               elapsed_s: float) -> GuardrailStatus:
        spent = foreman_spend_usd + harness_spend_usd
        frac_cost = spent / self.cost_ceiling_usd if self.cost_ceiling_usd else 0.0
        frac_time = elapsed_s / self.wall_clock_seconds if self.wall_clock_seconds else 0.0
        level, reason = "ok", "within budget"
        if frac_cost >= 1.0 or frac_time >= 1.0:
            level = "stop"
            reason = ("cost ceiling reached" if frac_cost >= 1.0
                      else "wall-clock ceiling reached")
        elif frac_cost >= self.warn_fraction or frac_time >= self.warn_fraction:
            level = "warn"
            reason = (f"cost at {frac_cost:.0%} of ${self.cost_ceiling_usd:.0f}"
                      if frac_cost >= self.warn_fraction
                      else f"wall-clock/time at {frac_time:.0%} of ceiling")
        return GuardrailStatus(
            spent_usd=round(spent, 6),
            remaining_usd=round(self.cost_ceiling_usd - spent, 6),
            elapsed_s=elapsed_s, frac_cost=frac_cost, frac_time=frac_time,
            level=level, reason=reason,
        )

    def can_afford_run(self, *, spent_usd: float) -> bool:
        """True only if a worst-case (per_run_max_cost_usd) run still fits the ceiling."""
        return spent_usd + self.per_run_max_cost_usd <= self.cost_ceiling_usd


def foreman_spend(scratch_root: Path | str) -> float:
    """Sum ``cost_usd`` across every ``runs/*/usage.json`` under the scratch repo.

    Authoritative and restart-safe. Tolerant of missing/garbage files so a
    half-written usage.json can never crash the guardrail check.
    """
    root = Path(scratch_root) / ".foreman" / "features"
    if not root.is_dir():
        return 0.0
    total = 0.0
    for usage in root.glob("*/runs/*/usage.json"):
        try:
            data = json.loads(usage.read_text())
            total += float(data.get("cost_usd", 0.0) or 0.0)
        except (ValueError, OSError):
            continue
    return total
