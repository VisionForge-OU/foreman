"""Load and validate ``.foreman/config.yaml`` (§10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .models import Budget

VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}
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
    effort: str = "high"
    required_skills: list[str] = field(default_factory=lambda: list(DEFAULT_REQUIRED_SKILLS))
    commands: dict[str, Optional[str]] = field(default_factory=dict)
    git: GitConfig = field(default_factory=GitConfig)
    limits: Limits = field(default_factory=Limits)
    run_budget: Budget = field(default_factory=Budget)
    stuck_turns: int = 12
    e2e_enabled: bool = True
    permission_mode: str = "acceptEdits"

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
        if self.git.merge_strategy not in VALID_MERGE:
            errs.append(f"git.merge_strategy must be one of {sorted(VALID_MERGE)}")
        if self.limits.max_parallel < 1:
            errs.append("limits.max_parallel must be >= 1")
        if self.limits.max_retries < 0:
            errs.append("limits.max_retries must be >= 0")
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
            "effort": self.effort,
            "required_skills": list(self.required_skills),
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
            "stuck_turns": self.stuck_turns,
            "e2e_enabled": self.e2e_enabled,
            "permission_mode": self.permission_mode,
        }


def from_dict(d: dict[str, Any]) -> Config:
    d = d or {}
    git = d.get("git", {}) or {}
    limits = d.get("limits", {}) or {}
    cfg = Config(
        model_planner=str(d.get("model_planner", "claude-fable-5")),
        model_worker=str(d.get("model_worker", "claude-fable-5")),
        effort=str(d.get("effort", "high")),
        required_skills=list(d.get("required_skills", DEFAULT_REQUIRED_SKILLS)),
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
        stuck_turns=int(d.get("stuck_turns", 12)),
        e2e_enabled=bool(d.get("e2e_enabled", True)),
        permission_mode=str(d.get("permission_mode", "acceptEdits")),
    )
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
