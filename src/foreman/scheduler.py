"""Phase B — the autonomous build loop / "Boris loop" (§7).

Picks ready issues, runs each through a TDD worker in its own git worktree with
budgets enforced, **independently re-runs** the configured commands to verify the
work (never trusting the agent), merges passing slices into the integration
branch, retries failures with feedback, and escalates exhausted retries / budget
breaches / stuck workers to the human attention queue. After all issues land it
runs the e2e phase if the PRD defines user flows.

Nothing here may run until the PRD is approved AND the issue queue is explicitly
confirmed (R3/§12) — enforced in :meth:`build`.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

from . import git_ops, vendored
from .backend import AgentBackend, RunSpec
from .config import Config
from .ledger import CostLedger
from .models import DocStatus, IssueStatus, Issue
from .runner import AgentRunner, RunResult, KILLED_USER
from .skill_invocation import SkillInvocation
from .state import FileStore
from .verify import verify
from .worktree import WorktreeManager


class SchedulerError(RuntimeError):
    pass


class Monitor(Protocol):
    """Optional observer for the TUI; all methods are best-effort."""

    def log(self, message: str) -> None: ...
    def worker_started(self, issue_id: str, run_id: str) -> None: ...
    def worker_event(self, issue_id: str, event) -> None: ...
    def worker_finished(self, issue_id: str, status: str, result: RunResult) -> None: ...
    def escalated(self, issue_id: str, reason: str) -> None: ...


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BuildReport:
    slug: str
    done: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    escalated: list[tuple[str, str]] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    total_cost_usd: float = 0.0
    e2e: Optional[str] = None
    stopped_reason: str = ""

    def render(self) -> str:
        lines = [f"# Build report — {self.slug}", ""]
        lines.append(f"- Merged: {', '.join(self.merged) or 'none'}")
        lines.append(f"- Done (not merged): {', '.join(self.done) or 'none'}")
        if self.escalated:
            lines.append("- Escalated to human:")
            lines += [f"    - {iid}: {reason}" for iid, reason in self.escalated]
        if self.blocked:
            lines.append(f"- Blocked (deps unmet): {', '.join(self.blocked)}")
        lines.append(f"- Total cost: ${self.total_cost_usd:.4f}")
        if self.e2e:
            lines.append(f"- E2E: {self.e2e}")
        if self.stopped_reason:
            lines.append(f"- Stopped early: {self.stopped_reason}")
        return "\n".join(lines) + "\n"


class Scheduler:
    def __init__(
        self,
        store: FileStore,
        config: Config,
        backend: AgentBackend,
        runner: Optional[AgentRunner] = None,
        *,
        worktrees: Optional[WorktreeManager] = None,
        ledger: Optional[CostLedger] = None,
        monitor: Optional[Monitor] = None,
        run_id_clock: Optional[Callable[[], str]] = None,
        verify_timeout_s: float = 600.0,
    ):
        self.store = store
        self.config = config
        self.backend = backend
        self.runner = runner or AgentRunner(backend)
        self.worktrees = worktrees or WorktreeManager(
            store.paths.root, config.git.integration_branch
        )
        self.ledger = ledger or CostLedger(store.paths.daily_cost_file)
        self.monitor = monitor
        self.verify_timeout_s = verify_timeout_s
        if run_id_clock is None:
            counter = itertools.count(1)
            run_id_clock = lambda: f"run{next(counter):04d}"  # noqa: E731
        self._run_id_clock = run_id_clock
        self._merge_lock = asyncio.Lock()
        self._cancels: dict[str, asyncio.Event] = {}
        self._paused = asyncio.Event()
        self._paused.set()  # not paused

    # ------------------------------------------------------------------ #
    # Control surface (TUI)
    # ------------------------------------------------------------------ #
    def pause(self) -> None:
        self._paused.clear()

    def unpause(self) -> None:
        self._paused.set()

    def kill_issue(self, issue_id: str) -> bool:
        ev = self._cancels.get(issue_id)
        if ev is not None:
            ev.set()
            return True
        return False

    def _log(self, msg: str) -> None:
        if self.monitor:
            self.monitor.log(msg)

    # ------------------------------------------------------------------ #
    # Preconditions (R3/§12)
    # ------------------------------------------------------------------ #
    def _precheck(self, slug: str) -> None:
        missing = vendored.missing_required(self.store.paths.root, self.config.required_skills)
        if missing:
            raise SchedulerError(f"required skill(s) missing: {', '.join(missing)}")
        state = self.store.load_feature(slug)
        prd = state.doc("prd")
        if prd is None or prd.status != DocStatus.APPROVED:
            raise SchedulerError("PRD is not approved — cannot start the build")
        if not state.queue_confirmed:
            raise SchedulerError("issue queue not confirmed — cannot start the build")
        if not state.issues:
            raise SchedulerError("no issues to build")

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    async def build(self, slug: str) -> BuildReport:
        self._precheck(slug)
        await self.worktrees.ensure_base()
        await self.worktrees.integration_worktree()

        report = BuildReport(slug=slug)
        running: dict[str, asyncio.Task] = {}

        while True:
            await self._paused.wait()
            if self.ledger.would_exceed(self.config.limits.daily_cost_usd):
                report.stopped_reason = (
                    f"daily cost ceiling reached "
                    f"(${self.ledger.spent_today():.2f} >= "
                    f"${self.config.limits.daily_cost_usd})"
                )
                self._log(report.stopped_reason)
                break

            state = self.store.load_feature(slug)
            ready = [i for i in state.ready_issues() if i.id not in running]
            for issue in ready:
                if len(running) >= self.config.limits.max_parallel:
                    break
                running[issue.id] = asyncio.create_task(self._work_issue(slug, issue))

            if not running:
                break  # nothing ready and nothing running

            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for iid in [i for i, t in running.items() if t in done]:
                task = running.pop(iid)
                exc = task.exception()
                if exc is not None:  # a worker crashed — escalate, never crash the loop
                    import traceback
                    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                    self._log(f"worker {iid} crashed:\n{tb}")
                    self._escalate(slug, self.store.load_issue(slug, iid), f"worker error: {exc!r}")

        # Drain any still-running workers (e.g. after a pause/ceiling stop).
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)

        self._tally(slug, report)
        await self._maybe_run_e2e(slug, report)
        self.store.paths.report_file(slug).write_text(report.render())
        return report

    def _tally(self, slug: str, report: BuildReport) -> None:
        state = self.store.load_feature(slug)
        for i in state.issues:
            if i.status == IssueStatus.MERGED:
                report.merged.append(i.id)
            elif i.status == IssueStatus.DONE:
                report.done.append(i.id)
            elif i.status == IssueStatus.NEEDS_HUMAN:
                reason = self._escalation_reason(slug, i.id)
                report.escalated.append((i.id, reason))
            elif i.status in (IssueStatus.QUEUED, IssueStatus.TESTS_FAILING):
                report.blocked.append(i.id)
        report.total_cost_usd = self.feature_cost(slug)

    # ------------------------------------------------------------------ #
    # Single issue lifecycle (with retries)
    # ------------------------------------------------------------------ #
    async def _work_issue(
        self, slug: str, issue: Issue, *, reviewer_answer: Optional[str] = None
    ) -> str:
        branch = issue.branch or f"feature/{slug}/{issue.id.lower()}"
        cancel = asyncio.Event()
        self._cancels[issue.id] = cancel
        self.store.update_issue_status(slug, issue.id, IssueStatus.IN_PROGRESS, branch=branch)
        wt = await self.worktrees.create_issue_worktree(issue.id, branch)
        failing_output: Optional[str] = None
        commands = self.config.commands
        try:
            while True:
                issue = self.store.load_issue(slug, issue.id)
                prompt = SkillInvocation.tdd(
                    issue, commands,
                    failing_output=failing_output, reviewer_answer=reviewer_answer,
                )
                reviewer_answer = None
                run_id = f"{self._run_id_clock()}-{issue.id}"
                spec = RunSpec(
                    kind="tdd", slug=slug, repo_root=self.store.paths.root, cwd=wt,
                    prompt=prompt, model=self.config.model_worker, effort=self.config.effort,
                    permission_mode=self.config.permission_mode, budget=issue.budget,
                    label=issue.id, extra_dirs=[self.store.paths.feature_dir(slug)],
                )
                if self.monitor:
                    self.monitor.worker_started(issue.id, run_id)
                result = await self.runner.run(
                    spec, run_id=run_id,
                    transcript_path=self.store.paths.run_transcript(slug, run_id),
                    on_event=(lambda e, iid=issue.id: self.monitor.worker_event(iid, e))
                    if self.monitor else None,
                    cancel_event=cancel,
                    stuck_turns=self.config.stuck_turns,
                )
                self.store.write_run_record(slug, result.record)
                if result.final_text:
                    self.store.write_run_summary(slug, run_id, result.final_text)
                self.ledger.add(result.record.cost_usd)

                # Killed by the user → roll back the worktree clean, requeue (§7).
                if result.record.terminal_reason == KILLED_USER:
                    await self.worktrees.rollback_and_remove(wt)
                    self.store.update_issue_status(slug, issue.id, IssueStatus.QUEUED)
                    if self.monitor:
                        self.monitor.worker_finished(issue.id, "killed", result)
                    return "killed"

                # Agent asked for help, or a budget/timeout/stuck kill → escalate.
                esc = result.escalation_reason
                if esc:
                    self._escalate(slug, issue, esc)
                    await self.worktrees.remove(wt)
                    if self.monitor:
                        self.monitor.worker_finished(issue.id, "needs_human", result)
                    return "escalated"

                # Independent verification — the trust boundary (§7/§12).
                vr = await verify(
                    wt, commands,
                    names=("test", "lint", "typecheck"),
                    timeout_s=self.verify_timeout_s,
                )
                claim = result.summary.claims_pass if result.summary else None

                if vr.passed:
                    await git_ops.commit_all(wt, f"{issue.id}: {issue.title}")
                    status = await self._merge(slug, issue, branch)
                    self.store.update_issue_status(slug, issue.id, status)
                    await self.worktrees.remove(wt)
                    if self.monitor:
                        self.monitor.worker_finished(issue.id, status.value, result)
                    return "done"

                # Verification failed — retry with feedback or escalate.
                attempts = issue.attempts + 1
                self.store.update_issue_status(
                    slug, issue.id, IssueStatus.TESTS_FAILING, attempts=attempts
                )
                note = ""
                if claim is True:
                    note = ("\n\n[Foreman] NOTE: your summary claimed the commands "
                            "passed, but Foreman's independent run disagrees:\n")
                failing_output = (vr.report() + "\n" + note + vr.failure_output())[:8000]
                if attempts >= self.config.limits.max_retries:
                    self._escalate(
                        slug, issue,
                        f"tests still failing after {attempts} attempt(s):\n{vr.report()}",
                    )
                    await self.worktrees.remove(wt)
                    if self.monitor:
                        self.monitor.worker_finished(issue.id, "needs_human", result)
                    return "escalated"
                # else loop and retry, keeping the worktree so the agent iterates.
        finally:
            self._cancels.pop(issue.id, None)

    async def _merge(self, slug: str, issue: Issue, branch: str) -> IssueStatus:
        async with self._merge_lock:
            integ = await self.worktrees.integration_worktree()
            res = await git_ops.merge_branch(
                integ, branch,
                strategy=self.config.git.merge_strategy,
                message=f"{issue.id}: {issue.title}",
            )
        if res.ok:
            return IssueStatus.MERGED
        self._log(f"merge of {branch} failed: {res.stderr.strip()[:200]}")
        return IssueStatus.DONE  # work is committed on its branch; merge needs a human

    # ------------------------------------------------------------------ #
    # Escalations (attention queue)
    # ------------------------------------------------------------------ #
    def _escalate(self, slug: str, issue: Issue, reason: str) -> None:
        self.store.paths.escalations_dir(slug).mkdir(parents=True, exist_ok=True)
        path = self.store.paths.escalation_file(slug, issue.id)
        existing = path.read_text() if path.exists() else ""
        path.write_text(
            existing
            + f"## Escalation @ {_utcnow()}\n\n{reason}\n\n"
            "<!-- Reviewer: add your answer below this line, then resume from the TUI. -->\n\n"
        )
        self.store.update_issue_status(slug, issue.id, IssueStatus.NEEDS_HUMAN)
        if self.monitor:
            self.monitor.escalated(issue.id, reason)
        self._log(f"⚠ {issue.id} escalated: {reason}")

    def _escalation_reason(self, slug: str, issue_id: str) -> str:
        path = self.store.paths.escalation_file(slug, issue_id)
        if not path.exists():
            return "needs human attention"
        # Return the first escalation reason line.
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "<!--")):
                return line
        return "needs human attention"

    def escalations(self, slug: str) -> list[tuple[str, str]]:
        state = self.store.load_feature(slug)
        return [
            (i.id, self._escalation_reason(slug, i.id))
            for i in state.issues
            if i.status == IssueStatus.NEEDS_HUMAN
        ]

    async def resume_issue(self, slug: str, issue_id: str, answer: str) -> str:
        """Answer an escalation and re-run the worker (§7)."""
        issue = self.store.load_issue(slug, issue_id)
        if issue is None:
            raise SchedulerError(f"no issue {issue_id}")
        path = self.store.paths.escalation_file(slug, issue_id)
        existing = path.read_text() if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(existing + f"\n### Reviewer answer @ {_utcnow()}\n\n{answer}\n")
        # Reset attempts so the human's answer gets a fresh retry budget.
        self.store.update_issue_status(slug, issue_id, IssueStatus.QUEUED, attempts=0)
        return await self._work_issue(slug, issue, reviewer_answer=answer)

    # ------------------------------------------------------------------ #
    # Cost
    # ------------------------------------------------------------------ #
    def feature_cost(self, slug: str) -> float:
        import json
        total = 0.0
        rdir = self.store.paths.runs_dir(slug)
        if not rdir.exists():
            return 0.0
        for usage in rdir.glob("*/usage.json"):
            try:
                total += float(json.loads(usage.read_text()).get("cost_usd", 0.0))
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        return round(total, 6)

    # ------------------------------------------------------------------ #
    # E2E (§7)
    # ------------------------------------------------------------------ #
    async def _maybe_run_e2e(self, slug: str, report: BuildReport) -> None:
        if not self.config.e2e_enabled:
            return
        state = self.store.load_feature(slug)
        prd = state.doc("prd")
        if prd is None or "user flows" not in prd.body.lower():
            return
        # Only run e2e if every issue actually landed.
        if any(i.status not in (IssueStatus.DONE, IssueStatus.MERGED) for i in state.issues):
            report.e2e = "skipped (not all issues landed)"
            return
        e2e_cmd = self.config.command("e2e")
        integ = await self.worktrees.integration_worktree()
        run_id = f"{self._run_id_clock()}-e2e"
        prompt = SkillInvocation.e2e(prd.body, e2e_cmd)
        spec = RunSpec(
            kind="e2e", slug=slug, repo_root=self.store.paths.root, cwd=integ,
            prompt=prompt, model=self.config.model_worker, effort=self.config.effort,
            permission_mode=self.config.permission_mode, budget=self.config.run_budget,
            label="e2e", extra_dirs=[self.store.paths.feature_dir(slug)],
        )
        if self.monitor:
            self.monitor.worker_started("e2e", run_id)
        result = await self.runner.run(
            spec, run_id=run_id,
            transcript_path=self.store.paths.run_transcript(slug, run_id),
            on_event=(lambda e: self.monitor.worker_event("e2e", e)) if self.monitor else None,
        )
        self.store.write_run_record(slug, result.record)
        if result.final_text:
            self.store.write_run_summary(slug, run_id, result.final_text)
        self.ledger.add(result.record.cost_usd)
        if e2e_cmd:
            async with self._merge_lock:
                await git_ops.commit_all(integ, "e2e tests")
            vr = await verify(integ, {"e2e": e2e_cmd}, names=("e2e",),
                              timeout_s=self.verify_timeout_s)
            report.e2e = "passed" if vr.passed else "failed"
        else:
            report.e2e = "ran (no e2e command configured to verify)"
        report.total_cost_usd = self.feature_cost(slug)
