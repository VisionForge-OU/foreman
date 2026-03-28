"""Load and validate ``.foreman/config.yaml`` (§10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .models import Budget

VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}
VALID_RETRY_STRATEGY = {"fresh", "resume"}
VALID_PERMISSION_MODES = {
    "acceptEdits", "auto", "default", "dontAsk", "plan", "bypassPermissions",
}
VALID_MERGE = {"merge", "squash", "rebase"}

DEFAULT_REQUIRED_SKILLS = [
    "foreman-grill-docs",
    "foreman-to-prd",
    "foreman-to-issues",
    "foreman-tdd",
]

DEFAULT_REQUIRED_AGENTS = [
    "foreman-evaluator",
    "foreman-auditor",
]


class ConfigError(ValueError):
    """Raised when a config file is invalid."""


@dataclass
class GitConfig:
    integration_branch: str = "main"
    merge_strategy: str = "merge"
    open_pr: bool = False


@dataclass
class Limits:
    max_parallel: int = 2
    max_retries: int = 3
    daily_cost_usd: float = 50.0


@dataclass
class Config:
    model_planner: str = "claude-fable-5"
    model_worker: str = "claude-fable-5"
    # WS2: the grader runs on a cheaper model by default; override per high-stakes issue.
    model_evaluator: str = "claude-haiku-4-5-20251001"
    effort: str = "high"
    required_skills: list[str] = field(default_factory=lambda: list(DEFAULT_REQUIRED_SKILLS))
    required_agents: list[str] = field(default_factory=lambda: list(DEFAULT_REQUIRED_AGENTS))
    commands: dict[str, Optional[str]] = field(default_factory=dict)
    git: GitConfig = field(default_factory=GitConfig)
    limits: Limits = field(default_factory=Limits)
    run_budget: Budget = field(default_factory=Budget)
    # WS2: the evaluator gets its own smaller budget.
    evaluator_budget: Budget = field(
        default_factory=lambda: Budget(max_turns=30, max_cost_usd=2.0, timeout_min=20)
    )
    evaluator_enabled: bool = True
    evaluator_min_score: int = 3
    # WS5: the read-only spec-integrity auditor + low-fatigue review notifications.
    auditor_enabled: bool = True
    model_auditor: str = "claude-haiku-4-5-20251001"
    notify_command: Optional[str] = None
    # WS6: bench (harness regression testing) settings.
    bench_eval_set: str = ".foreman/eval_set"
    bench_cost_ceiling_usd: float = 5.0
    # WS3.3: fresh-session retries by default (never resume a failed context).
    retry_strategy: str = "fresh"
    # WS4.3: run a janitor pass after every N merged feature issues.
    janitor_enabled: bool = True
    janitor_every: int = 3
    janitor_kinds: list[str] = field(default_factory=lambda: ["dedup", "conventions", "docs"])
    stuck_turns: int = 12
    e2e_enabled: bool = True
    permission_mode: str = "acceptEdits"
    # Turn-budget extensions: when an agent/worker runs out of turns (or a worker
    # asks via request_more_turns) Foreman can resume the SAME session with more
    # turns up to ``max_turn_extensions`` times before escalating to a human.
    auto_extend_turns: bool = True
    max_turn_extensions: int = 2
    turn_extension_size: int = 0      # 0 ⇒ reuse run_budget.max_turns per extension

    # ---- accessors used by the runner / scheduler ----
    def command(self, name: str) -> Optional[str]:
        """A configured command (test/lint/typecheck/e2e), or None if unset/empty."""
        val = self.commands.get(name)
        if val is None:
            return None
        val = str(val).strip()
        return val or None

    def validate(self) -> None:
        errs: list[str] = []
        if self.effort not in VALID_EFFORT:
            errs.append(f"effort must be one of {sorted(VALID_EFFORT)}, got {self.effort!r}")
        if self.permission_mode not in VALID_PERMISSION_MODES:
            errs.append(
                f"permission_mode must be one of {sorted(VALID_PERMISSION_MODES)}, "
                f"got {self.permission_mode!r}"
            )
        if self.retry_strategy not in VALID_RETRY_STRATEGY:
            errs.append(
                f"retry_strategy must be one of {sorted(VALID_RETRY_STRATEGY)}, "
                f"got {self.retry_strategy!r}"
            )
        if self.git.merge_strategy not in VALID_MERGE:
            errs.append(f"git.merge_strategy must be one of {sorted(VALID_MERGE)}")
        if self.limits.max_parallel < 1:
            errs.append("limits.max_parallel must be >= 1")
        if self.limits.max_retries < 0:
            errs.append("limits.max_retries must be >= 0")
        if self.max_turn_extensions < 0:
            errs.append("max_turn_extensions must be >= 0")
        if self.turn_extension_size < 0:
            errs.append("turn_extension_size must be >= 0")
        if self.limits.daily_cost_usd <= 0:
            errs.append("limits.daily_cost_usd must be > 0")
        if not self.required_skills:
            errs.append("required_skills must not be empty")
        if errs:
            raise ConfigError("; ".join(errs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_planner": self.model_planner,
            "model_worker": self.model_worker,
            "model_evaluator": self.model_evaluator,
            "effort": self.effort,
            "required_skills": list(self.required_skills),
            "required_agents": list(self.required_agents),
            "commands": dict(self.commands),
            "git": {
                "integration_branch": self.git.integration_branch,
                "merge_strategy": self.git.merge_strategy,
                "open_pr": self.git.open_pr,
            },
            "limits": {
                "max_parallel": self.limits.max_parallel,
                "max_retries": self.limits.max_retries,
                "daily_cost_usd": self.limits.daily_cost_usd,
            },
            "run_budget": self.run_budget.to_dict(),
            "evaluator_budget": self.evaluator_budget.to_dict(),
            "evaluator_enabled": self.evaluator_enabled,
            "evaluator_min_score": self.evaluator_min_score,
            "auditor_enabled": self.auditor_enabled,
            "model_auditor": self.model_auditor,
            "notify_command": self.notify_command,
            "bench_eval_set": self.bench_eval_set,
            "bench_cost_ceiling_usd": self.bench_cost_ceiling_usd,
            "retry_strategy": self.retry_strategy,
            "janitor_enabled": self.janitor_enabled,
            "janitor_every": self.janitor_every,
            "janitor_kinds": list(self.janitor_kinds),
            "stuck_turns": self.stuck_turns,
            "e2e_enabled": self.e2e_enabled,
            "permission_mode": self.permission_mode,
            "auto_extend_turns": self.auto_extend_turns,
            "max_turn_extensions": self.max_turn_extensions,
            "turn_extension_size": self.turn_extension_size,
        }


def from_dict(d: dict[str, Any]) -> Config:
    d = d or {}
    git = d.get("git", {}) or {}
    limits = d.get("limits", {}) or {}
    cfg = Config(
        model_planner=str(d.get("model_planner", "claude-fable-5")),
        model_worker=str(d.get("model_worker", "claude-fable-5")),
        model_evaluator=str(d.get("model_evaluator", "claude-haiku-4-5-20251001")),
        effort=str(d.get("effort", "high")),
        required_skills=list(d.get("required_skills", DEFAULT_REQUIRED_SKILLS)),
        required_agents=list(d.get("required_agents", DEFAULT_REQUIRED_AGENTS)),
        commands=dict(d.get("commands", {}) or {}),
        git=GitConfig(
            integration_branch=str(git.get("integration_branch", "main")),
            merge_strategy=str(git.get("merge_strategy", "merge")),
            open_pr=bool(git.get("open_pr", False)),
        ),
        limits=Limits(
            max_parallel=int(limits.get("max_parallel", 2)),
            max_retries=int(limits.get("max_retries", 3)),
            daily_cost_usd=float(limits.get("daily_cost_usd", 50.0)),
        ),
        run_budget=Budget.from_dict(d.get("run_budget")),
        evaluator_enabled=bool(d.get("evaluator_enabled", True)),
        evaluator_min_score=int(d.get("evaluator_min_score", 3)),
        auditor_enabled=bool(d.get("auditor_enabled", True)),
        model_auditor=str(d.get("model_auditor", "claude-haiku-4-5-20251001")),
        notify_command=(str(d["notify_command"]).strip() or None)
        if d.get("notify_command") else None,
        bench_eval_set=str(d.get("bench_eval_set", ".foreman/eval_set")),
        bench_cost_ceiling_usd=float(d.get("bench_cost_ceiling_usd", 5.0)),
        retry_strategy=str(d.get("retry_strategy", "fresh")),
        janitor_enabled=bool(d.get("janitor_enabled", True)),
        janitor_every=int(d.get("janitor_every", 3)),
        janitor_kinds=list(d.get("janitor_kinds", ["dedup", "conventions", "docs"])),
        stuck_turns=int(d.get("stuck_turns", 12)),
        e2e_enabled=bool(d.get("e2e_enabled", True)),
        permission_mode=str(d.get("permission_mode", "acceptEdits")),
        auto_extend_turns=bool(d.get("auto_extend_turns", True)),
        max_turn_extensions=int(d.get("max_turn_extensions", 2)),
        turn_extension_size=int(d.get("turn_extension_size", 0)),
    )
    if d.get("evaluator_budget"):
        cfg.evaluator_budget = Budget.from_dict(d.get("evaluator_budget"))
    return cfg


def load(path: Path | str) -> Config:
    """Load and validate a config file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    cfg = from_dict(data)
    cfg.validate()
    return cfg
