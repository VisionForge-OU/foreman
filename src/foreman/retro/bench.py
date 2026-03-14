"""``foreman bench`` — replay an eval set and produce a delta report (WS6).

An eval set is a list of known-good cases (issue + repo snapshot + expected
outcome). ``foreman bench`` replays each case — *mocked by default* (no tokens,
MockBackend-style replay) with an optional real-token mode behind a cost ceiling —
and produces a success-rate / cost / turn report. :meth:`BenchReport.delta`
diffs a report against a baseline. The report is attached next to every retro
patch proposal so the rule *"no skill patch lands without a bench report"*
(WS6, see :func:`foreman.retro.retro.is_landable`) is enforceable.

The actual run-a-case mechanism is injected (``runner_factory``) so this module
stays above the backend seam and is unit-testable with a fake runner. Anything
skipped by the cost ceiling is LOGGED — never silently capped.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("foreman.retro.bench")


# --------------------------------------------------------------------------- #
# Eval set
# --------------------------------------------------------------------------- #
@dataclass
class BenchCase:
    """One eval case: an issue against a repo snapshot with a known-good outcome."""

    name: str
    repo_snapshot: Path
    issue: dict[str, Any]
    expected_outcome: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo_snapshot": str(self.repo_snapshot),
            "issue": dict(self.issue),
            "expected_outcome": self.expected_outcome,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BenchCase":
        d = d or {}
        return cls(
            name=str(d.get("name", "") or ""),
            repo_snapshot=Path(str(d.get("repo_snapshot", "") or "")),
            issue=dict(d.get("issue", {}) or {}),
            expected_outcome=str(d.get("expected_outcome", "") or ""),
        )


def seed_eval_set(dir: Path, cases: list[BenchCase]) -> Path:
    """Write an eval set as a ``cases.jsonl`` file (one case per line). Returns it.

    Used by tests and to capture past runs as a regression eval set.
    """
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / "cases.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case.to_dict()) + "\n")
    return path


def load_eval_set(dir: Path) -> list[BenchCase]:
    """Load an eval set from ``<dir>/cases.jsonl`` (preferred) or per-case dirs.

    Tolerant: skips blank / unparseable lines. If no ``cases.jsonl`` exists, it
    reads ``<dir>/<case>/case.json`` directories instead.
    """
    dir = Path(dir)
    cases: list[BenchCase] = []
    jsonl = dir / "cases.jsonl"
    if jsonl.exists():
        for line in jsonl.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                cases.append(BenchCase.from_dict(data))
        return cases
    if dir.exists():
        for case_dir in sorted(p for p in dir.iterdir() if p.is_dir()):
            case_json = case_dir / "case.json"
            if not case_json.exists():
                continue
            try:
                data = json.loads(case_json.read_text())
            except ValueError:
                continue
            if isinstance(data, dict):
                data.setdefault("name", case_dir.name)
                data.setdefault("repo_snapshot", str(case_dir))
                cases.append(BenchCase.from_dict(data))
    return cases


# --------------------------------------------------------------------------- #
# Results & report
# --------------------------------------------------------------------------- #
@dataclass
class BenchResult:
    """The outcome of replaying one case."""

    name: str
    outcome: str
    cost_usd: float = 0.0
    turns: int = 0
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "cost_usd": self.cost_usd,
            "turns": self.turns,
            "passed": self.passed,
        }


@dataclass
class BenchReport:
    """Aggregate of a bench run + delta against a baseline."""

    results: list[BenchResult] = field(default_factory=list)
    success_rate: float = 0.0
    total_cost: float = 0.0
    mean_turns: float = 0.0
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "success_rate": self.success_rate,
            "total_cost": self.total_cost,
            "mean_turns": self.mean_turns,
            "skipped": list(self.skipped),
        }

    def delta(self, baseline: "BenchReport") -> dict[str, float]:
        """Success-rate / cost / turn deltas vs a baseline report."""
        return {
            "success_rate": self.success_rate - baseline.success_rate,
            "total_cost": self.total_cost - baseline.total_cost,
            "mean_turns": self.mean_turns - baseline.mean_turns,
        }

    def render(self) -> str:
        lines = [
            "Bench report",
            f"  cases:          {len(self.results)}",
            f"  success rate:   {self.success_rate * 100:.0f}%",
            f"  total cost:     ${self.total_cost:.2f}",
            f"  mean turns:     {self.mean_turns:.1f}",
        ]
        for r in self.results:
            mark = "✓" if r.passed else "✗"
            lines.append(
                f"    {mark} {r.name:<24} {r.outcome:<22} "
                f"${r.cost_usd:.2f} {r.turns}t"
            )
        if self.skipped:
            lines.append(f"  skipped (cost ceiling): {', '.join(self.skipped)}")
        return "\n".join(lines)


def _report_from_results(
    results: list[BenchResult], skipped: list[str]
) -> BenchReport:
    n = len(results)
    success_rate = (sum(1 for r in results if r.passed) / n) if n else 0.0
    total_cost = sum(r.cost_usd for r in results)
    mean_turns = (sum(r.turns for r in results) / n) if n else 0.0
    return BenchReport(
        results=results,
        success_rate=success_rate,
        total_cost=total_cost,
        mean_turns=mean_turns,
        skipped=skipped,
    )


# A runner_factory turns a case into an awaitable yielding a partial result dict
# (or a BenchResult). Injected so tests can supply a fake and so the real path
# (MockBackend replay or live tokens) lives in the scheduler/runner above the seam.
RunnerFactory = Callable[[BenchCase], Awaitable[Any]]


def _coerce_result(case: BenchCase, raw: Any) -> BenchResult:
    """Turn a runner's return value into a graded :class:`BenchResult`."""
    if isinstance(raw, BenchResult):
        result = raw
    else:
        d = raw if isinstance(raw, dict) else {}
        result = BenchResult(
            name=case.name,
            outcome=str(d.get("outcome", "") or ""),
            cost_usd=float(d.get("cost_usd", 0.0) or 0.0),
            turns=int(d.get("turns", d.get("num_turns", 0)) or 0),
        )
    # Grade against the known-good expected outcome (stem comparison).
    result.passed = _outcome_matches(result.outcome, case.expected_outcome)
    return result


def _outcome_matches(actual: str, expected: str) -> bool:
    if not expected:
        return False
    a = (actual or "").split("(", 1)[0].strip()
    e = expected.split("(", 1)[0].strip()
    return a == e


async def run_bench(
    eval_set: list[BenchCase],
    *,
    runner_factory: RunnerFactory,
    mocked: bool = True,
    cost_ceiling_usd: Optional[float] = None,
) -> BenchReport:
    """Replay each case and aggregate a :class:`BenchReport`.

    - ``mocked=True`` (default) is the no-token MockBackend-style path; the
      injected ``runner_factory`` decides the actual mechanism per case.
    - ``cost_ceiling_usd`` stops dispatching further cases once accumulated cost
      would exceed it; every skipped case is LOGGED and recorded in
      ``report.skipped`` (no silent caps, WS6).
    """
    results: list[BenchResult] = []
    skipped: list[str] = []
    spent = 0.0
    for case in eval_set:
        if cost_ceiling_usd is not None and spent >= cost_ceiling_usd:
            skipped.append(case.name)
            logger.warning(
                "bench: skipping case %s — cost ceiling $%.2f reached (spent $%.2f)",
                case.name, cost_ceiling_usd, spent,
            )
            continue
        raw = await runner_factory(case)
        result = _coerce_result(case, raw)
        # In mocked mode replayed runs report no real spend; in real mode the
        # runner's cost counts against the ceiling for the NEXT case.
        if not mocked:
            spent += result.cost_usd
        results.append(result)
    report = _report_from_results(results, skipped)
    if skipped:
        logger.info("bench: %d case(s) skipped by cost ceiling: %s",
                    len(skipped), ", ".join(skipped))
    return report


# --------------------------------------------------------------------------- #
# Attaching a report to a proposal (enforces "no patch lands without a bench")
# --------------------------------------------------------------------------- #
def attach_report(proposal_dir: Path, report: BenchReport) -> Path:
    """Persist a bench report next to a retro patch proposal. Returns the path."""
    proposal_dir = Path(proposal_dir)
    proposal_dir.mkdir(parents=True, exist_ok=True)
    path = proposal_dir / "bench_report.json"
    path.write_text(json.dumps(report.to_dict(), indent=2))
    return path
