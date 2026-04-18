"""Controller — the non-UI glue between the TUI and the orchestration core.

Holds the store/config/backend/pipeline/scheduler and exposes the actions the TUI
triggers, plus in-memory live buffers (worker logs, cost, turns) that the UI
polls on an interval. Kept free of Textual imports so it is unit-testable.
"""

from __future__ import annotations

import tempfile
import time
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


@dataclass
class Activity:
    """The single top-level thing Foreman is doing right now, for the status line.

    Covers the otherwise-silent Phase-A agents (planner/grill/slicer) as well as the
    build loop, so the TUI can always say what is happening instead of looking frozen.
    """

    kind: str                 # planner | grill | slicer | build | resume
    label: str                # display label
    started: float            # time.monotonic() at start
    turns: int = 0            # assistant turns seen so far (live)
    last_line: str = ""       # most recent humanized activity line
    running: bool = True

    def elapsed_s(self, now: Optional[float] = None) -> int:
        return int((now if now is not None else time.monotonic()) - self.started)


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
                                 self.runner, run_id_clock=_run_id,
                                 event_sink=self._on_phase_event)
        self.scheduler = Scheduler(self.store, self.config, self.backend, self.runner,
                                   ledger=CostLedger(self.store.paths.daily_cost_file),
                                   monitor=self, run_id_clock=_run_id)
        # live buffers
        self.workers: dict[str, WorkerLog] = {}
        self.global_log: list[str] = []
        self.bell_pending = False
        # What Foreman is doing right now (drives the TUI status line). None = idle.
        self.activity: Optional[Activity] = None
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
        if len(self.global_log) > 1000:
            self.global_log = self.global_log[-1000:]
        self._emit(message)

    # ------------------------------------------------------------------ #
    # Current-activity tracking (drives the status line; §8.3)
    # ------------------------------------------------------------------ #
    def begin_activity(self, kind: str, label: str) -> None:
        self.activity = Activity(kind=kind, label=label, started=time.monotonic())
        self.log(f"▶ {label} started")

    def end_activity(self, ok: bool, note: str = "") -> None:
        if self.activity is not None:
            self.activity.running = False
            verb = "done" if ok else "failed"
            self.log(f"■ {self.activity.label} {verb}" + (f" — {note}" if note else ""))
        self.activity = None

    def _on_phase_event(self, event: StreamEvent) -> None:
        """Pipeline event sink: keep the status line + global log live for the
        otherwise-silent Phase-A agents (planner/grill/slicer)."""
        if isinstance(event, AssistantMessage) and self.activity is not None:
            self.activity.turns += 1
        line = humanize(event)
        if not line:
            return
        for sub in line.splitlines():
            sub = sub.strip()
            if not sub:
                continue
            if self.activity is not None:
                self.activity.last_line = sub[:160]
            self.log(sub)

    def status_line(self) -> str:
        """One-line summary of what Foreman is doing right now (markup-free)."""
        a = self.activity
        if a is None:
            return "idle"
        secs = a.elapsed_s()
        if a.kind in ("build", "resume"):
            running = [w for w in self.workers.values() if w.status == "running"]
            if running:
                parts = [f"{w.issue_id} {w.turns}t ${w.cost:.2f}" for w in running[:3]]
                more = f" +{len(running) - 3}" if len(running) > 3 else ""
                return f"{a.label} · {len(running)} worker(s) · {' · '.join(parts)}{more} · {secs}s"
            return f"{a.label} · preparing… · {secs}s"
        tail = f" · {a.last_line}" if a.last_line else " · working…"
        return f"{a.label} · turn {a.turns} · {secs}s{tail}"

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

    # ------------------------------------------------------------------ #
    # Retro proposals (WS6.2/6.3 / H7) — the patch-approval flywheel surface
    # ------------------------------------------------------------------ #
    def retro_proposals(self):
        """All gated retro proposals on disk (for the TUI review screen)."""
        from ..retro import driver
        out = []
        for name in driver.list_names(self.store):
            sp = driver.load(self.store, name)
            if sp is not None:
                out.append(sp)
        return out

    def retro_proposal(self, name: str):
        from ..retro import driver
        return driver.load(self.store, name)

    def proposal_bench_text(self, name: str) -> str:
        from ..retro import driver
        rep = driver.bench_report(self.store, name)
        if not rep:
            return "Bench: (none attached — required before landing; run `foreman bench`)"
        return (f"Bench: success_rate={rep.get('success_rate')}  "
                f"mean_turns={rep.get('mean_turns')}  "
                f"cases={len(rep.get('results') or [])}")

    def proposal_detail(self, name: str) -> str:
        """Status + bench delta + the sealed review body (diff + rationale) — H7."""
        from .. import frontmatter
        path = self.store.paths.retro_proposal_file(name)
        if not path.exists():
            return f"{name}: (no such proposal)"
        sp = self.retro_proposal(name)
        seal = " · sealed" if (sp and sp.sealed) else ""
        head = f"[{sp.status if sp else '?'}{seal}]  target: {sp.proposal.target if sp else '?'}\n"
        body = frontmatter.parse(path.read_text()).body
        return head + self.proposal_bench_text(name) + "\n\n" + body

    def approve_proposal(self, name: str):
        from ..retro import driver
        return driver.approve(self.store, name)

    def reject_proposal(self, name: str):
        from ..retro import driver
        return driver.reject(self.store, name)

    def land_proposal(self, name: str) -> str:
        """Land an approved+benched proposal; raises ValueError if the gate fails."""
        from ..retro import driver
        return driver.land(self.store, name)

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
        isolated = sorted(i.id for i in issues if not graph.get(i.id))
        if isolated:
            lines.append(f"  no overlaps: {', '.join(isolated)}")
        return "\n".join(lines)

    def queue_review_text(self, slug: str) -> str:
        """Queue-review detail: schema-critical fields plus the conflict graph."""
        issues = [i for i in self.feature(slug).issues if not i.is_janitor]
        if not issues:
            return "Queue review\n  (no issues)"

        lines = ["Queue review"]
        for issue in issues:
            deps = ", ".join(issue.depends_on) if issue.depends_on else "(none)"
            prd_refs = ", ".join(issue.prd_refs) if issue.prd_refs else "MISSING"
            touches = ", ".join(issue.touches) if issue.touches else "MISSING"
            check = issue.acceptance_check.strip() if issue.acceptance_check else "MISSING"
            lines.extend([
                f"{issue.id} — {issue.title}",
                f"  depends_on: {deps}",
                f"  acceptance_check: {check}",
                f"  touches: {touches}",
                f"  prd_refs: {prd_refs}",
            ])
        summary = self.conflict_summary(slug)
        if summary:
            lines.extend(["", summary])
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Actions (sync where possible; async for agent work)
    # ------------------------------------------------------------------ #
    def create_feature(self, title: str, request: str) -> str:
        return self.store.create_feature(title, request)

    def approve(self, slug: str, kind: str) -> None:
        self.store.approve_doc(slug, kind, reviewer="reviewer")

    def request_changes(self, slug: str, kind: str, comments: str) -> Optional[list[str]]:
        """Record a changes-requested review. Returns a list of new fix-issue ids
        when this rejects an auto-drafted PRD amendment (WS5.1/H6), else None."""
        # WS5.2: snapshot the body the reviewer acted on, so the next draft can be
        # shown as a diff-since-last-review.
        doc = self.feature(slug).doc(kind)
        if doc is not None:
            self.store.paths.reviews_dir(slug).mkdir(parents=True, exist_ok=True)
            (self.store.paths.reviews_dir(slug) / f"{kind}-v{doc.version}-body.md").write_text(doc.body)
        # WS5.1/H6: rejecting an auto-drafted PRD amendment is NOT a silent drop —
        # it keeps the approved spec and spins the divergence(s) into fix issues.
        from ..audit import AMENDMENT_HEADING
        if kind == "prd" and doc is not None and AMENDMENT_HEADING in doc.body:
            created = self.scheduler.reject_amendment(slug, comments)
            if created:
                self.log(f"rejected PRD amendment → created {len(created)} fix "
                         f"issue(s): {', '.join(created)}")
                return created
        self.store.request_changes(slug, kind, reviewer="reviewer", comments=comments)
        return None

    def confirm_queue(self, slug: str) -> None:
        self.store.confirm_queue(slug)

    async def _tracked(self, kind: str, label: str, coro):
        self.begin_activity(kind, label)
        try:
            result = await coro
            self.end_activity(True)
            return result
        except Exception as e:  # surface the failure on the status line, then re-raise
            self.end_activity(False, str(e).splitlines()[0][:80] if str(e) else "")
            raise

    async def run_planner(self, slug: str):
        return await self._tracked("planner", "planner", self.pipeline.run_planner(slug))

    async def run_grill(self, slug: str):
        return await self._tracked("grill", "grill (ADR+PRD)", self.pipeline.run_grill(slug))

    async def run_slicer(self, slug: str):
        return await self._tracked("slicer", "slicer", self.pipeline.run_slicer(slug))

    async def build(self, slug: str):
        return await self._tracked("build", "build", self.scheduler.build(slug))

    async def resume(self, slug: str, issue_id: str, answer: str):
        return await self._tracked(
            "resume", f"resume {issue_id}", self.scheduler.resume_issue(slug, issue_id, answer)
        )
