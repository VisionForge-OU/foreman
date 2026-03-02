"""Controller — the non-UI glue between the TUI and the orchestration core.

Holds the store/config/backend/pipeline/scheduler and exposes the actions the TUI
triggers, plus in-memory live buffers (worker logs, cost, turns) that the UI
polls on an interval. Kept free of Textual imports so it is unit-testable.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import vendored
from ..backend import ClaudeBackend, MockBackend
from ..config import Config, load as load_config
from ..ledger import CostLedger
from ..models import FeatureState
from ..pipeline import Pipeline
from ..runner import AgentRunner
from ..scheduler import Scheduler
from ..state import FileStore
from ..stream_parser import (
    AssistantMessage, ResultEvent, StreamEvent, humanize,
)


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


@dataclass
class WorkerLog:
    issue_id: str
    run_id: str = ""
    status: str = "running"
    lines: list[str] = field(default_factory=list)
    cost: float = 0.0
    turns: int = 0

    def append(self, line: str) -> None:
        self.lines.append(line)
        if len(self.lines) > 500:
            self.lines = self.lines[-500:]


class Controller:
    def __init__(self, repo_root: Path | str, *, demo: bool = False):
        self.demo = demo
        if demo:
            self.repo_root = self._setup_demo(repo_root)
        else:
            self.repo_root = Path(repo_root).resolve()

        self.store = FileStore(self.repo_root)
        self.config = self._load_config()
        self.backend = self._build_backend()
        self.runner = AgentRunner(self.backend)
        self.pipeline = Pipeline(self.store, self.config, self.backend,
                                 self.runner, run_id_clock=_run_id)
        self.scheduler = Scheduler(self.store, self.config, self.backend, self.runner,
                                   ledger=CostLedger(self.store.paths.daily_cost_file),
                                   monitor=self, run_id_clock=_run_id)
        # live buffers
        self.workers: dict[str, WorkerLog] = {}
        self.global_log: list[str] = []
        self.bell_pending = False
        # optional callback for headless/CLI live logging
        self.log_sink = None

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #
    def _setup_demo(self, repo_root) -> Path:
        from ..installer import init_repo
        from ..sample import create_sample_repo

        base = Path(tempfile.mkdtemp(prefix="foreman-demo-"))
        repo = create_sample_repo(base / "todo-cli")
        init_repo(repo)
        return repo

    def _load_config(self) -> Config:
        if self.demo:
            from ..sample import pytest_command
            cfg = Config()
            cfg.commands = {"test": pytest_command(), "lint": "", "typecheck": "",
                            "e2e": pytest_command()}
            cfg.stuck_turns = 0
            return cfg
        if self.store.paths.config_file.exists():
            return load_config(self.store.paths.config_file)
        return Config()

    def _build_backend(self):
        if self.demo:
            from ..demo_scripts import demo_scripts
            return MockBackend(demo_scripts(fail_first_issue="ISS-001"))
        return ClaudeBackend()

    # ------------------------------------------------------------------ #
    # Monitor interface (called by the scheduler)
    # ------------------------------------------------------------------ #
    def _emit(self, message: str) -> None:
        if self.log_sink is not None:
            self.log_sink(message)

    def log(self, message: str) -> None:
        self.global_log.append(message)
        self._emit(message)

    def worker_started(self, issue_id: str, run_id: str) -> None:
        wl = self.workers.setdefault(issue_id, WorkerLog(issue_id))
        wl.run_id = run_id
        wl.status = "running"
        wl.append(f"── run {run_id} ──")
        self._emit(f"  ▶ worker {issue_id} started ({run_id})")

    def worker_event(self, issue_id: str, event: StreamEvent) -> None:
        wl = self.workers.setdefault(issue_id, WorkerLog(issue_id))
        if isinstance(event, AssistantMessage):
            wl.turns += 1
        elif isinstance(event, ResultEvent):
            wl.cost = event.total_cost_usd or wl.cost
        line = humanize(event)
        if line:
            for sub in line.splitlines():
                wl.append(sub)

    def worker_finished(self, issue_id: str, status: str, result) -> None:
        wl = self.workers.setdefault(issue_id, WorkerLog(issue_id))
        wl.status = status
        wl.cost = result.record.cost_usd or wl.cost
        wl.append(f"■ finished: {status} (${wl.cost:.4f}, {result.record.num_turns} turns)")
        self._emit(f"  ■ worker {issue_id}: {status} "
                   f"(${wl.cost:.4f}, {result.record.num_turns} turns)")

    def escalated(self, issue_id: str, reason: str) -> None:
        self.bell_pending = True
        self.global_log.append(f"⚠ {issue_id}: {reason}")
        self._emit(f"  ⚠ {issue_id} escalated: {reason}")

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def features(self) -> list[str]:
        return self.store.list_features()

    def feature(self, slug: str) -> FeatureState:
        return self.store.load_feature(slug)

    def skills_status(self):
        return vendored.status(self.repo_root)

    def missing_required(self):
        return vendored.missing_required(self.repo_root, self.config.required_skills)

    def feature_cost(self, slug: str) -> float:
        return self.scheduler.feature_cost(slug)

    def escalations(self, slug: str):
        return self.scheduler.escalations(slug)

    # ------------------------------------------------------------------ #
    # Actions (sync where possible; async for agent work)
    # ------------------------------------------------------------------ #
    def create_feature(self, title: str, request: str) -> str:
        return self.store.create_feature(title, request)

    def approve(self, slug: str, kind: str) -> None:
        self.store.approve_doc(slug, kind, reviewer="reviewer")

    def request_changes(self, slug: str, kind: str, comments: str) -> None:
        self.store.request_changes(slug, kind, reviewer="reviewer", comments=comments)

    def confirm_queue(self, slug: str) -> None:
        self.store.confirm_queue(slug)

    async def run_planner(self, slug: str):
        return await self.pipeline.run_planner(slug)

    async def run_grill(self, slug: str):
        return await self.pipeline.run_grill(slug)

    async def run_slicer(self, slug: str):
        return await self.pipeline.run_slicer(slug)

    async def build(self, slug: str):
        return await self.scheduler.build(slug)

    async def resume(self, slug: str, issue_id: str, answer: str):
        return await self.scheduler.resume_issue(slug, issue_id, answer)
