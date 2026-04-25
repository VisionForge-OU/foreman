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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

from . import (
    audit as audit_mod, conflicts, git_ops, janitor as janitor_mod,
    locks, notify as notify_mod, prd, prompts, vendored,
)
from .agents import evaluator as evaluator_mod
from .agents import installer as agents_installer
from .backend import AgentBackend, RunSpec
from .context import initializer
from .context.assembler import ContextAssembler
from .config import Config
from .issue_run import IssueRun
from .ledger import CostLedger
from .models import DocStatus, IssueStatus, Issue
from .runner import AgentRunner, RunResult, should_extend
from .skill_invocation import SkillInvocation
from .state import FileStore
from .verify import verify
from .verification import checks, ratchet
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
    janitor: list[tuple[str, str, str]] = field(default_factory=list)  # (id, kind, outcome)
    total_cost_usd: float = 0.0
    retries: int = 0   # total retry attempts across feature issues (beyond first try)
    e2e: Optional[str] = None
    audit: Optional[str] = None  # WS5.1: satisfied | amendment_drafted(n) | ...
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
        if self.janitor:
            lines.append("- Janitor passes:")
            lines += [f"    - {iid} ({kind}): {outcome}" for iid, kind, outcome in self.janitor]
        lines.append(f"- Total cost: ${self.total_cost_usd:.4f}")
        lines.append(f"- Retries: {self.retries}   ·   Escalations: {len(self.escalated)}")
        if self.e2e:
            lines.append(f"- E2E: {self.e2e}")
        if self.audit:
            lines.append(f"- Spec audit: {self.audit}")
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
        self.assembler = ContextAssembler()  # WS3.4: the single prompt builder
        self._initialized: set[str] = set()   # features whose initializer has run
        self._lock_blocked: set[str] = set()  # WS4.2: issues blocked by a foreign lock
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
        if self.config.evaluator_enabled:
            from .agents import installer as agents_installer
            missing_agents = agents_installer.missing(
                self.store.paths.root, self.config.required_agents
            )
            if missing_agents:
                raise SchedulerError(
                    f"required agent(s) missing: {', '.join(missing_agents)} — run `foreman init`"
                )
        state = self.store.load_feature(slug)
        adr = state.doc("adr")
        prd = state.doc("prd")
        if adr is None or adr.status != DocStatus.APPROVED:
            raise SchedulerError("ADR is not approved — cannot start the build")
        if prd is None or prd.status != DocStatus.APPROVED:
            raise SchedulerError("PRD is not approved — cannot start the build")
        if not state.queue_confirmed:
            raise SchedulerError("issue queue not confirmed — cannot start the build")
        if not state.issues:
            raise SchedulerError("no issues to build")
        # WS1.1: no issue may enter the build without a runnable acceptance check.
        missing_checks = checks.issues_missing_checks(state.issues)
        if missing_checks:
            raise SchedulerError(
                "issue(s) missing a runnable acceptance_check (WS1.1): "
                + ", ".join(missing_checks)
            )
        # Seed the Default-FAIL structural-done map (idempotent, P2.2/WS1.2).
        self.store.seed_verification(slug)

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    async def build(self, slug: str) -> BuildReport:
        self._precheck(slug)
        # Keep the repo's vendored foreman-* skills/agents current (idempotent): after a
        # Foreman upgrade they go stale, and re-running `foreman init` by hand every time
        # is pure friction. These are Foreman-owned and git-excluded (below), so refresh
        # them in place. The worker/evaluator also get the packaged copies in each
        # worktree, but the auditor/e2e run in the integration worktree (the repo).
        refreshed = (vendored.install(self.store.paths.root)
                     + agents_installer.install(self.store.paths.root))
        if refreshed:
            self._log(f"refreshed vendored skills/agents: {', '.join(refreshed)}")
        await self.worktrees.ensure_base()
        integ = await self.worktrees.integration_worktree()
        # WS4.2: keep the lock dir out of git, and reclaim any dead workers' locks.
        git_ops.ensure_excluded(self.store.paths.root, "current_tasks/")
        # Keep the Foreman-provisioned skills/agents (installed into each worktree so
        # the worker/evaluator can find them) out of `git add -A`, so they never leak
        # into a merge. Scoped to the foreman-* names — the user's own .claude is untouched.
        git_ops.ensure_excluded(self.store.paths.root, ".claude/skills/foreman-*")
        git_ops.ensure_excluded(self.store.paths.root, ".claude/agents/foreman-*")
        reclaimed = locks.reclaim_stale(integ)
        if reclaimed:
            self._log(f"reclaimed stale task lock(s): {', '.join(reclaimed)}")
        # R4 crash recovery: no worker is running in this fresh process yet, so any
        # issue resting in a mid-flight status is an orphan from a previous, now-dead
        # run (e.g. Foreman was SIGKILLed). Reset it to QUEUED — preserving its
        # attempt count and its already-flipped verification — and drop its stale
        # task lock, so the build resumes it instead of silently stalling.
        recovered = self._reconcile_orphans(slug, integ)
        if recovered:
            self._log(f"recovered orphaned in-flight issue(s) after restart: "
                      f"{', '.join(recovered)}")
        await self._run_initializer(slug)  # WS3.1: one-time per-feature bootstrap

        report = BuildReport(slug=slug)
        running: dict[str, asyncio.Task] = {}
        self._lock_blocked = set()  # WS4.2: issues a live foreign lock blocks this run
        self._janitor_passes = 0    # WS4.3: janitor passes run so far this build

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
            ready = [i for i in state.ready_issues()
                     if i.id not in running and i.id not in self._lock_blocked
                     and not i.is_janitor]  # janitors run via the cadence, not here
            # WS4.1: never co-schedule issues whose declared footprints overlap.
            running_issues = [i for i in state.issues if i.id in running]
            slots = self.config.limits.max_parallel - len(running)
            to_start = conflicts.pick_dispatch(ready, running_issues, slots)
            for issue in to_start:
                running[issue.id] = asyncio.create_task(self._work_issue(slug, issue))

            if not running:
                # WS4.3: with no feature worker in flight, run a due janitor pass.
                if await self._maybe_run_janitor(slug, report):
                    continue
                break  # nothing ready, nothing running, no janitor due

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
        await self._maybe_run_auditor(slug, report)  # WS5.1: spec-integrity audit
        self.store.write_report(slug, report.render())
        return report

    # Mid-flight statuses: a worker was actively on the issue when the process died.
    # These are never resting states across builds, so finding one at build start
    # means the owning worker is gone. (DONE/MERGED/NEEDS_HUMAN/QUEUED are resting.)
    _ORPHAN_STATES = (
        IssueStatus.IN_PROGRESS,
        IssueStatus.TESTS_FAILING,
        IssueStatus.AWAITING_EVALUATION,
    )

    def _reconcile_orphans(self, slug: str, integ) -> list[str]:
        """Requeue issues left mid-flight by a crashed run (R4). No-op normally."""
        recovered: list[str] = []
        for issue in self.store.load_feature(slug).issues:
            if issue.status in self._ORPHAN_STATES:
                # Keep attempts so the retry ceiling still applies; a fresh worktree
                # is forked on re-dispatch (worktree.create_issue_worktree cleans up).
                self.store.update_issue_status(slug, issue.id, IssueStatus.QUEUED)
                locks.release(integ, issue.id)
                recovered.append(issue.id)
        return recovered

    def _tally(self, slug: str, report: BuildReport) -> None:
        state = self.store.load_feature(slug)
        for i in state.issues:
            if i.is_janitor:
                continue  # janitor outcomes are tracked in report.janitor (WS4.3)
            if i.status == IssueStatus.MERGED:
                report.merged.append(i.id)
            elif i.status == IssueStatus.DONE:
                report.done.append(i.id)
            elif i.status == IssueStatus.NEEDS_HUMAN:
                reason = self._escalation_reason(slug, i.id)
                report.escalated.append((i.id, reason))
            elif i.status in (IssueStatus.QUEUED, IssueStatus.TESTS_FAILING):
                report.blocked.append(i.id)
        report.retries = sum(i.attempts for i in state.issues if not i.is_janitor)
        report.total_cost_usd = self.feature_cost(slug)

    # ------------------------------------------------------------------ #
    # Single issue lifecycle (with retries)
    # ------------------------------------------------------------------ #
    async def _work_issue(
        self, slug: str, issue: Issue, *, reviewer_answer: Optional[str] = None,
        janitor_kind: Optional[str] = None,
    ) -> str:
        """Run one issue's full lifecycle through its own worktree (delegated to
        :class:`~foreman.issue_run.IssueRun`). Returns the terminal outcome string."""
        return await IssueRun(
            self, slug, issue,
            reviewer_answer=reviewer_answer, janitor_kind=janitor_kind,
        ).run()

    async def _run_agent_with_extensions(
        self, slug: str, *, label: str, monitor_id: Optional[str], base_budget,
        build_spec, continuation: str = "",
    ):
        """Run a non-worker agent (evaluator / e2e / auditor) with bounded turn-budget
        extensions: on a hard turn cut-off, resume the SAME session with more turns up
        to ``max_turn_extensions`` before giving up — so a long evaluation/audit/e2e
        isn't lost to the budget. Persists each run's record + summary and accrues cost.

        ``build_spec(session_id, budget) -> RunSpec`` builds the spec per attempt.
        Returns ``(final RunResult, final run_id)``.
        """
        extensions = 0
        session_id: Optional[str] = None
        while True:
            run_id = f"{self._run_id_clock()}-{label}"
            if extensions > 0:
                budget = replace(
                    base_budget,
                    max_turns=(self.config.turn_extension_size or base_budget.max_turns),
                )
            else:
                budget = base_budget
            spec = build_spec(session_id, budget)
            if extensions and continuation:
                spec = replace(spec, prompt=continuation + "\n\n" + spec.prompt)
            if self.monitor and monitor_id:
                self.monitor.worker_started(monitor_id, run_id)
            result = await self.runner.run(
                spec, run_id=run_id,
                transcript_path=self.store.paths.run_transcript(slug, run_id),
                on_event=(lambda e, mid=monitor_id: self.monitor.worker_event(mid, e))
                if (self.monitor and monitor_id) else None,
            )
            self.store.write_run_record(slug, result.record)
            if result.final_text:
                self.store.write_run_summary(slug, run_id, result.final_text)
            self.ledger.add(result.record.cost_usd)

            if should_extend(
                result.record.terminal_reason,
                has_session=bool(result.record.session_id),
                extensions=extensions,
                max_extensions=self.config.max_turn_extensions,
                auto_extend=self.config.auto_extend_turns,
            ):
                extensions += 1
                session_id = result.record.session_id
                self._log(f"  ↻ {label}: turn extension "
                          f"{extensions}/{self.config.max_turn_extensions} (cut off) — "
                          "resuming the same session to finish")
                continue
            return result, run_id

    async def _evaluate(
        self, slug: str, issue: Issue, wt: Path, gate, evidence_dir: Path
    ) -> Optional[evaluator_mod.Verdict]:
        """Spawn the read-only evaluator from a fresh context; parse + store its verdict."""
        state = self.store.load_feature(slug)
        prd_doc = state.doc("prd")
        prd_sections = prd.extract_sections(prd_doc.body, issue.prd_refs) if prd_doc else ""
        diff = await git_ops.diff_against(wt, self.config.git.integration_branch)
        prompt = evaluator_mod.build_prompt(
            issue, prd_sections=prd_sections, diff=diff, worktree=wt,
            evidence_dir=evidence_dir, evidence_artifacts=gate.evidence_artifacts,
        )
        def _build(session_id, budget):
            return RunSpec(
                kind="evaluator", slug=slug, repo_root=self.store.paths.root, cwd=wt,
                prompt=prompt, model=self.config.model_evaluator, effort=self.config.effort,
                permission_mode=self.config.permission_mode, budget=budget,
                label=f"{issue.id}-eval", agent=evaluator_mod.AGENT_NAME,
                extra_dirs=[self.store.paths.feature_dir(slug)], session_id=session_id,
            )
        result, run_id = await self._run_agent_with_extensions(
            slug, label=f"{issue.id}-eval", monitor_id=f"{issue.id}-eval",
            base_budget=self.config.evaluator_budget, build_spec=_build,
            continuation=prompts.agent_continuation(
                "grading this slice and emit the required "
                "verdict JSON block, then stop. Do not start over."),
        )

        verdict = evaluator_mod.parse(
            result.final_text, min_score=self.config.evaluator_min_score
        )
        # Persist the verdict for the TUI worker view and the run record (WS2.4).
        self._write_verdict(slug, run_id, verdict, result.final_text)
        if self.monitor:
            v = verdict.verdict if verdict else "unparseable"
            self.monitor.worker_finished(f"{issue.id}-eval", f"verdict:{v}", result)
            self._log(f"⚖ {issue.id} evaluator verdict: {v}")
        return verdict

    def _stamp_outcome(self, slug: str, result, label: str) -> None:
        """WS6: record the run's outcome-taxonomy label and re-persist the record."""
        result.record.outcome = label
        self.store.write_run_record(slug, result.record)

    def _write_verdict(self, slug: str, run_id: str, verdict, final_text: str) -> None:
        payload = verdict.raw if verdict else {"schema": "foreman-verdict/v1",
                                               "verdict": "unparseable",
                                               "raw_text": final_text[:2000]}
        self.store.write_run_verdict(slug, run_id, payload)

    # ------------------------------------------------------------------ #
    # WS3: context architecture (initializer, init.sh, minimal prompts)
    # ------------------------------------------------------------------ #
    def _prd_sections(self, slug: str, issue: Issue) -> str:
        prd_doc = self.store.load_feature(slug).doc("prd")
        return prd.extract_sections(prd_doc.body, issue.prd_refs) if prd_doc else ""

    async def _run_init_sh(self, slug: str, wt: Path) -> None:
        """Run the feature bootstrap in the worktree before a worker (best-effort)."""
        init_path = self.store.paths.init_script(slug)
        if not init_path.exists():
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", str(init_path), cwd=str(wt),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except (OSError, ValueError):
            pass  # bootstrap failures must not block the build

    async def _run_initializer(self, slug: str) -> None:
        """Spawn the one-time per-feature initializer (WS3.1), with a fallback."""
        if slug in self._initialized:
            return
        fs_path = self.store.paths.feature_state_file(slug)
        init_path = self.store.paths.init_script(slug)
        already = (fs_path.exists() and fs_path.read_text().strip()
                   and init_path.exists() and init_path.read_text().strip())
        if not already:
            state = self.store.load_feature(slug)
            prompt = initializer.build_prompt(
                slug=slug, request=state.request, commands=self.config.commands,
                init_path=init_path, feature_state_path=fs_path,
            )
            run_id = f"{self._run_id_clock()}-init"
            spec = RunSpec(
                kind="initializer", slug=slug, repo_root=self.store.paths.root,
                cwd=self.store.paths.root, prompt=prompt, model=self.config.model_planner,
                effort=self.config.effort, permission_mode=self.config.permission_mode,
                budget=self.config.run_budget, label="init",
                extra_dirs=[self.store.paths.feature_dir(slug)],
            )
            if self.monitor:
                self.monitor.worker_started("init", run_id)
            try:
                result = await self.runner.run(
                    spec, run_id=run_id,
                    transcript_path=self.store.paths.run_transcript(slug, run_id),
                )
                self.store.write_run_record(slug, result.record)
                if result.final_text:
                    self.store.write_run_summary(slug, run_id, result.final_text)
                self.ledger.add(result.record.cost_usd)
                if self.monitor:
                    self.monitor.worker_finished("init", "done", result)
            except Exception as e:  # initializer failure must not block the build
                self._log(f"initializer failed ({e!r}); using deterministic fallback")
            # Ensure both artifacts exist regardless of what the agent produced.
            initializer.validate_and_fallback(
                slug=slug, request=state.request, commands=self.config.commands,
                init_path=init_path, feature_state_path=fs_path,
            )
        self._initialized.add(slug)

    # ------------------------------------------------------------------ #
    # WS4.3: specialist janitor passes
    # ------------------------------------------------------------------ #
    async def _maybe_run_janitor(self, slug: str, report: BuildReport) -> bool:
        """Run a janitor pass if one is due (every N merged feature issues). Returns
        True if a pass ran (so the build loop should continue)."""
        if not self.config.janitor_enabled or self.config.janitor_every <= 0:
            return False
        state = self.store.load_feature(slug)
        merged_feature = sum(
            1 for i in state.issues if i.status == IssueStatus.MERGED and not i.is_janitor
        )
        due = merged_feature // self.config.janitor_every
        if due <= self._janitor_passes:
            return False
        await self._run_janitor_pass(slug, report)
        self._janitor_passes += 1
        return True

    async def _run_janitor_pass(self, slug: str, report: BuildReport) -> None:
        """Run each specialist janitor one at a time through the full pipeline."""
        self._log(f"🧹 janitor pass #{self._janitor_passes + 1}")
        for key in self.config.janitor_kinds:
            if key not in janitor_mod.KINDS:
                continue
            state = self.store.load_feature(slug)
            n = sum(1 for i in state.issues if i.is_janitor) + 1
            iid = f"JAN-{n:03d}"
            branch = f"janitor/{slug}/{iid.lower()}"
            issue = janitor_mod.make_issue(key, issue_id=iid, branch=branch)
            self.store.write_issue(slug, issue)
            self.store.seed_verification(slug)
            try:
                outcome = await self._work_issue(slug, issue, janitor_kind=key)
            except Exception as e:  # a janitor failure must never sink the build
                outcome = f"error: {e!r}"
                self._log(f"janitor {iid} ({key}) crashed: {e!r}")
            report.janitor.append((iid, key, outcome))

    async def _land(self, slug: str, issue: Issue, branch: str, gate, wt: Path) -> IssueStatus:
        """Commit, merge, and snapshot the regression-ratchet baseline (WS1.4)."""
        await git_ops.commit_all(wt, f"{issue.id}: {issue.title}")
        status = await self._merge(slug, issue, branch)
        if status == IssueStatus.MERGED:
            ratchet.update_baseline(self.store.paths.baseline_file(slug), gate.now)
        return status

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
        self.store.append_escalation(
            slug, issue.id,
            f"## Escalation @ {_utcnow()}\n\n{reason}\n\n"
            "<!-- Reviewer: add your answer below this line, then resume from the TUI. -->\n\n",
        )
        self.store.update_issue_status(slug, issue.id, IssueStatus.NEEDS_HUMAN)
        if self.monitor:
            self.monitor.escalated(issue.id, reason)
        self._log(f"⚠ {issue.id} escalated: {reason}")
        # WS5.3: fire the configured notification so the human can step away.
        notify_mod.fire(self.config.notify_command, event="escalation",
                        feature=slug, ref=issue.id, reason=reason)

    def _escalation_reason(self, slug: str, issue_id: str) -> str:
        text = self.store.read_escalation(slug, issue_id)
        if not text:
            return "needs human attention"
        # Return the first escalation reason line.
        for line in text.splitlines():
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
        self.store.append_escalation(
            slug, issue_id, f"\n### Reviewer answer @ {_utcnow()}\n\n{answer}\n"
        )
        # Reset attempts so the human's answer gets a fresh retry budget.
        self.store.update_issue_status(slug, issue_id, IssueStatus.QUEUED, attempts=0)
        return await self._work_issue(slug, issue, reviewer_answer=answer)

    # ------------------------------------------------------------------ #
    # Cost
    # ------------------------------------------------------------------ #
    def feature_cost(self, slug: str) -> float:
        total = 0.0
        for rec in self.store.usage_records(slug):
            try:
                total += float(rec.get("cost_usd", 0.0))
            except (TypeError, ValueError):
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
        prompt = SkillInvocation.e2e(prd.body, e2e_cmd)

        def _build(session_id, budget):
            return RunSpec(
                kind="e2e", slug=slug, repo_root=self.store.paths.root, cwd=integ,
                prompt=prompt, model=self.config.model_worker, effort=self.config.effort,
                permission_mode=self.config.permission_mode, budget=budget,
                label="e2e", extra_dirs=[self.store.paths.feature_dir(slug)],
                session_id=session_id,
            )
        await self._run_agent_with_extensions(
            slug, label="e2e", monitor_id="e2e", base_budget=self.config.run_budget,
            build_spec=_build,
            continuation=prompts.agent_continuation("the e2e flow and stop."),
        )
        if e2e_cmd:
            async with self._merge_lock:
                await git_ops.commit_all(integ, "e2e tests")
            vr = await verify(integ, {"e2e": e2e_cmd}, names=("e2e",),
                              timeout_s=self.verify_timeout_s)
            report.e2e = "passed" if vr.passed else "failed"
        else:
            report.e2e = "ran (no e2e command configured to verify)"
        report.total_cost_usd = self.feature_cost(slug)

    # ------------------------------------------------------------------ #
    # WS5.1: spec-integrity auditor (implementation ↔ PRD divergence)
    # ------------------------------------------------------------------ #
    async def _maybe_run_auditor(self, slug: str, report: BuildReport) -> None:
        if not self.config.auditor_enabled:
            return
        state = self.store.load_feature(slug)
        prd_doc = state.doc("prd")
        if prd_doc is None or prd_doc.status != DocStatus.APPROVED:
            return
        if not state.issues or any(
            i.status not in (IssueStatus.DONE, IssueStatus.MERGED)
            for i in state.issues if not i.is_janitor
        ):
            return  # only audit a fully-landed feature
        integ = await self.worktrees.integration_worktree()
        prompt = audit_mod.build_prompt(prd_doc.body, worktree=integ, e2e_summary=report.e2e or "")

        def _build(session_id, budget):
            return RunSpec(
                kind="auditor", slug=slug, repo_root=self.store.paths.root, cwd=integ,
                prompt=prompt, model=self.config.model_auditor, effort=self.config.effort,
                permission_mode=self.config.permission_mode, budget=budget,
                label="audit", agent=audit_mod.AGENT_NAME,
                extra_dirs=[self.store.paths.feature_dir(slug)], session_id=session_id,
            )
        result, run_id = await self._run_agent_with_extensions(
            slug, label="audit", monitor_id="audit",
            base_budget=self.config.evaluator_budget, build_spec=_build,
            continuation=prompts.agent_continuation(
                "the audit and emit the required audit JSON "
                "block, then stop. Do not start over."),
        )

        rep = audit_mod.parse(result.final_text)
        self._write_audit(slug, run_id, rep, result.final_text)

        if rep is not None and rep.needs_amendment:
            # Re-seal the spec: a new IN_REVIEW PRD version auto-invalidates the prior
            # approval at load (R3), so the amendment re-enters the human gate (WS5.1).
            amended = audit_mod.build_amendment(prd_doc.body, rep)
            self.store.write_doc(slug, "prd", amended, status=DocStatus.IN_REVIEW)
            report.audit = f"amendment_drafted ({len(rep.divergences)} divergence(s))"
            notify_mod.fire(self.config.notify_command, event="review_needed",
                            feature=slug, ref="prd",
                            reason=f"auditor drafted a PRD amendment "
                                   f"({len(rep.divergences)} divergence(s))")
            if self.monitor:
                self.monitor.worker_finished("audit", "amendment_drafted", result)
                self.monitor.escalated("prd", "spec divergence — PRD amendment needs review")
        else:
            report.audit = "all requirements satisfied" if (rep and rep.all_satisfied) \
                else "no divergence (audit produced no amendment)"
            if self.monitor:
                self.monitor.worker_finished("audit", "satisfied", result)
        report.total_cost_usd = self.feature_cost(slug)

    def _write_audit(self, slug: str, run_id: str, report, final_text: str) -> None:
        payload = report.raw if report else {
            "schema": "foreman-audit/v1", "status": "unparseable",
            "raw_text": final_text[:2000],
        }
        self.store.write_run_audit(slug, run_id, payload)

    def _load_latest_audit(self, slug: str):
        """The most recent parseable audit report on disk (None if none)."""
        for raw in self.store.audit_payloads(slug):
            rep = audit_mod.report_from_raw(raw)
            if rep is not None:
                return rep
        return None

    def reject_amendment(self, slug: str, comments: str = "") -> list[str]:
        """Reject an auto-drafted PRD amendment (WS5.1, H6).

        The reviewer is saying *the spec is right; the code diverged* — so the
        approved PRD stands and each divergence becomes a concrete, buildable fix
        issue instead of a silent drop. Returns the new issue ids. A no-op (returns
        ``[]``) when the current PRD carries no amendment or no audit can be found,
        so the caller can fall back to an ordinary ``request_changes``.
        """
        state = self.store.load_feature(slug)
        prd_doc = state.doc("prd")
        if prd_doc is None or audit_mod.AMENDMENT_HEADING not in prd_doc.body:
            return []
        rep = self._load_latest_audit(slug)
        if rep is None:
            return []
        bodies = audit_mod.fix_issue_bodies(rep)
        if not bodies:
            return []
        # Record the rejection for the audit trail.
        self.store.request_changes(
            slug, "prd", reviewer="reviewer",
            comments=comments or "rejected PRD amendment → fix issues",
        )
        # The approved spec stands: strip the amendment section and re-seal the PRD.
        original = prd_doc.body.split(audit_mod.AMENDMENT_HEADING)[0].rstrip() + "\n"
        self.store.write_doc(slug, "prd", original, status=DocStatus.IN_REVIEW)
        self.store.approve_doc(slug, "prd", "reviewer")
        # Spin off the fix issues (queued, with a runnable acceptance check so the
        # WS1.1 gate lets them build; unknown footprint ⇒ they run alone, safely).
        test_cmd = (self.config.command("test") or "").strip() or "true"
        existing = sum(1 for i in state.issues if i.id.startswith("FIX-"))
        created: list[str] = []
        for n, b in enumerate(bodies, start=existing + 1):
            iid = f"FIX-{n:03d}"
            self.store.write_issue(slug, Issue(
                id=iid, title=b["title"], status=IssueStatus.QUEUED,
                body=b["body"], acceptance_check=test_cmd,
            ))
            created.append(iid)
        self.store.seed_verification(slug)  # Default-FAIL entries for the new issues
        self._log(f"rejected PRD amendment → created fix issue(s): {', '.join(created)}")
        return created
