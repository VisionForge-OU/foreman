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
    prompt_tokens: int = 0  # WS3.4: assembled-prompt size, for context-bloat visibility

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
        wl.prompt_tokens = result.record.prompt_tokens or wl.prompt_tokens
        tok = f", {wl.prompt_tokens} ctx-tok" if wl.prompt_tokens else ""
        wl.append(f"■ finished: {status} (${wl.cost:.4f}, {result.record.num_turns} turns{tok})")
        self._emit(f"  ■ worker {issue_id}: {status} "
                   f"(${wl.cost:.4f}, {result.record.num_turns} turns{tok})")

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

    def agents_status(self):
        from ..agents import installer as agents_installer
        return agents_installer.status(self.repo_root)

    def missing_required(self):
        return vendored.missing_required(self.repo_root, self.config.required_skills)

    def feature_cost(self, slug: str) -> float:
        return self.scheduler.feature_cost(slug)

    def escalations(self, slug: str):
        return self.scheduler.escalations(slug)

    def feature_metrics_text(self, slug: str) -> str:
        """WS6.1: the metrics pane for a feature (success rate, retries, cost, escalations)."""
        from ..retro import metrics
        return metrics.render(metrics.load_feature_metrics(self.store, slug))

    def metrics_trend_text(self) -> str:
        from ..retro import metrics
        per = [metrics.load_feature_metrics(self.store, s) for s in self.store.list_features()]
        return metrics.trend(per)

    def review_badges(self, slug: str, kind: str) -> str:
        """WS5.2: read-time + word-delta triage badges for a doc awaiting review."""
        from .. import review
        doc = self.feature(slug).doc(kind)
        if doc is None:
            return ""
        prior = self._last_reviewed_body(slug, kind, doc.version)
        b = review.badges(prior, doc.body)
        return f"≈{b['read_min']:.1f} min · {b['word_delta']:+d} words"

    def review_diff(self, slug: str, kind: str) -> str:
        """WS5.2: diff since the version the reviewer last acted on (else full body)."""
        from .. import review
        doc = self.feature(slug).doc(kind)
        if doc is None:
            return ""
        prior = self._last_reviewed_body(slug, kind, doc.version)
        d = review.diff_since(prior, doc.body)
        return d or doc.body

    def _last_reviewed_body(self, slug: str, kind: str, current_version: int) -> str:
        """Body of the most recent version the reviewer acted on (for diffing)."""
        for v in range(current_version - 1, 0, -1):
            rev = self.store.latest_review(slug, kind, v)
            if rev is not None:
                snap = self.store.paths.reviews_dir(slug) / f"{kind}-v{v}-body.md"
                if snap.exists():
                    return snap.read_text()
        return ""

    def conflict_summary(self, slug: str) -> str:
        """A compact conflict-graph summary for the queue-review screen (WS4.1)."""
        from .. import conflicts
        issues = [i for i in self.feature(slug).issues if not i.is_janitor]
        if not issues:
            return ""
        graph = conflicts.conflict_graph(issues)
        unknown = [i.id for i in issues if not i.footprint_known]
        edges = sorted({tuple(sorted((a, b))) for a, nb in graph.items() for b in nb})
        lines = [f"Conflict graph: {len(edges)} overlapping pair(s)"]
        if unknown:
            lines.append(f"  ⚠ unknown footprint (runs alone): {', '.join(unknown)}")
        for a, b in edges[:8]:
            lines.append(f"  {a} ↔ {b}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Actions (sync where possible; async for agent work)
    # ------------------------------------------------------------------ #
    def create_feature(self, title: str, request: str) -> str:
        return self.store.create_feature(title, request)

    def approve(self, slug: str, kind: str) -> None:
        self.store.approve_doc(slug, kind, reviewer="reviewer")

    def request_changes(self, slug: str, kind: str, comments: str) -> None:
        # WS5.2: snapshot the body the reviewer acted on, so the next draft can be
        # shown as a diff-since-last-review.
        doc = self.feature(slug).doc(kind)
        if doc is not None:
            self.store.paths.reviews_dir(slug).mkdir(parents=True, exist_ok=True)
            (self.store.paths.reviews_dir(slug) / f"{kind}-v{doc.version}-body.md").write_text(doc.body)
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
