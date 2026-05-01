"""IssueRun — one issue's full build lifecycle behind ``run() -> str`` (deepening 2).

Extracted from the Boris loop so the scheduler is a dispatcher and a single issue's
lifecycle — lock lease, per-attempt worker run, turn-budget extension, mandatory
handoff, the merge gate, retry/escalation, and worktree teardown — lives (and can
be tested) in one place. It is a method object over the :class:`Scheduler`: ``self.s``
is the owning scheduler, whose collaborators (store, runner, worktrees, ledger,
config, assembler, monitor) and small policy methods (``_evaluate``, ``_escalate``,
``_land``, ``_stamp_outcome``, ``_prd_sections``, ``_run_init_sh``) it reuses.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Optional

from . import git_ops, hooks, janitor as janitor_mod, locks, prompts, vendored
from .agents import installer as agents_installer
from .backend import RunSpec
from .context import distiller, initializer
from .context.assembler import AssembledPrompt, estimate_tokens
from .models import Issue, IssueStatus
from .retro import metrics as metrics_mod
from .runner import should_extend, KILLED_USER, KILLED_TURNS
from .verification import merge_gate


class IssueRun:
    """Runs one issue (feature or janitor) to a terminal outcome.

    Returns one of ``"done" | "escalated" | "killed" | "blocked"`` — the same
    contract the scheduler's loop, janitor pass, and ``resume_issue`` expect.
    """

    def __init__(
        self, sched, slug: str, issue: Issue, *,
        reviewer_answer: Optional[str] = None, janitor_kind: Optional[str] = None,
    ):
        self.s = sched
        self.slug = slug
        self.issue = issue
        self.reviewer_answer = reviewer_answer
        self.janitor_kind = janitor_kind

    async def run(self) -> str:
        slug = self.slug
        issue = self.issue
        reviewer_answer = self.reviewer_answer
        janitor_kind = self.janitor_kind

        branch = issue.branch or f"feature/{slug}/{issue.id.lower()}"
        # WS4.2: take the crash-safe per-issue lock FIRST — before any destructive
        # worktree work. The issue worktree path is shared per issue id, so a second
        # worker for the same issue (a resume overlapping a build, or two builds) must
        # back off here rather than clobbering the live worker's worktree via
        # create_issue_worktree (which removes + recreates that path).
        integ = await self.s.worktrees.integration_worktree()
        lock_run = f"{issue.id}-{self.s._run_id_clock()}"
        if not locks.acquire(integ, issue.id, run_id=lock_run):
            self.s._log(f"{issue.id}: task lock held by a live worker — backing off")
            self.s.store.update_issue_status(slug, issue.id, IssueStatus.QUEUED)
            self.s._lock_blocked.add(issue.id)  # don't re-dispatch this build (no spin)
            return "blocked"
        cancel = asyncio.Event()
        self.s._cancels[issue.id] = cancel
        self.s.store.update_issue_status(slug, issue.id, IssueStatus.IN_PROGRESS, branch=branch)
        try:
            wt = await self.s.worktrees.create_issue_worktree(issue.id, branch)
            # The worktree is forked from the integration branch, which usually does NOT
            # have the (often-untracked) vendored foreman-* skills/agents committed — so
            # the worker couldn't find the `foreman-tdd` skill and the evaluator couldn't
            # run as `foreman-evaluator`. Provision them into each worktree; they're
            # git-excluded (see build()) so they never leak into the merge.
            vendored.install(wt)
            agents_installer.install(wt)
            # WS1.3/1.5: install the per-worktree deny hook + foreman-test wrapper.
            hookinst = hooks.install(
                wt, test_command=self.s.config.command("test"), worker_id=issue.id
            )
        except BaseException:
            # Don't leak the per-issue lock if worktree/hook setup fails (it now runs
            # AFTER acquiring the lock, so a setup failure must release it).
            locks.release(integ, issue.id)
            self.s._cancels.pop(issue.id, None)
            raise
        commands = self.s.config.commands
        eval_bounces = 0  # WS2: count evaluator objections to detect repeated disagreement
        # WS3.3: carry a distilled failure report + handoff across fresh retries.
        failure_report = ""
        prior_progress = ""
        prior_session_id: Optional[str] = None
        # Turn-budget extensions used so far this run (in-memory: a crash tears down
        # the session/worktree, so the count is meaningless after).
        turn_extensions = 0
        prd_sections = self.s._prd_sections(slug, issue)
        feature_state = initializer.read_feature_state(
            self.s.store.paths.feature_state_file(slug)
        )
        try:
            while True:
                issue = self.s.store.load_issue(slug, issue.id)
                locks.heartbeat(integ, issue.id, run_id=lock_run)  # WS4.2: prove liveness
                run_id = f"{self.s._run_id_clock()}-{issue.id}"
                evidence_dir = self.s.store.paths.run_evidence_dir(slug, run_id)
                evidence_dir.mkdir(parents=True, exist_ok=True)
                # WS3.1: every session runs the feature bootstrap first.
                await self.s._run_init_sh(slug, wt)
                # Turn-budget extension: this loop continues the SAME session with a
                # fresh turn allowance, rather than a fresh-context retry.
                if turn_extensions > 0:
                    ext_turns = (self.s.config.turn_extension_size
                                 or self.s.config.run_budget.max_turns)
                    run_budget = replace(issue.budget, max_turns=ext_turns)
                    resume_id = prior_session_id  # force resume regardless of retry_strategy
                else:
                    ext_turns = 0
                    run_budget = issue.budget
                    # WS3.3: fresh session by default; `resume` continues prior context.
                    resume_id = (prior_session_id
                                 if self.s.config.retry_strategy == "resume" else None)
                if janitor_kind:  # WS4.3: specialist janitor prompt, same pipeline
                    jtext = janitor_mod.build_prompt(
                        issue, janitor_kind, evidence_dir=evidence_dir,
                        feature_state=feature_state,
                    )
                    jtext = prompts.with_failure_report(jtext, failure_report)
                    assembled = AssembledPrompt(
                        text=jtext, breakdown={"janitor": estimate_tokens(jtext)}
                    )
                else:
                    assembled = self.s.assembler.worker_prompt(
                        issue, commands, evidence_dir=evidence_dir,
                        prd_sections=prd_sections, feature_state=feature_state,
                        progress=prior_progress, failure_report=failure_report,
                        reviewer_answer=reviewer_answer or "",
                        turns=run_budget.max_turns,
                    )
                reviewer_answer = None
                prompt_text = assembled.text
                if ext_turns:
                    prompt_text = prompts.worker_continuation(ext_turns) + prompt_text
                spec = RunSpec(
                    kind="janitor" if janitor_kind else "tdd",
                    slug=slug, repo_root=self.s.store.paths.root, cwd=wt,
                    prompt=prompt_text, model=self.s.config.model_worker,
                    effort=self.s.config.effort,
                    permission_mode=self.s.config.permission_mode, budget=run_budget,
                    label=issue.id, extra_dirs=[self.s.store.paths.feature_dir(slug)],
                    settings_path=hookinst.settings_path, env=hookinst.env,
                    session_id=resume_id,
                )
                if self.s.monitor:
                    self.s.monitor.worker_started(issue.id, run_id)
                    self.s._log(f"  ▸ {issue.id} prompt {assembled.total_tokens} tok "
                                f"{dict(assembled.breakdown)}")
                result = await self.s.runner.run(
                    spec, run_id=run_id,
                    transcript_path=self.s.store.paths.run_transcript(slug, run_id),
                    on_event=(lambda e, iid=issue.id: self.s.monitor.worker_event(iid, e))
                    if self.s.monitor else None,
                    cancel_event=cancel,
                    stuck_turns=self.s.config.stuck_turns,
                )
                result.record.prompt_tokens = assembled.total_tokens  # WS3.4 visibility
                prior_session_id = result.record.session_id
                self.s.store.write_run_record(slug, result.record)
                if result.final_text:
                    self.s.store.write_run_summary(slug, run_id, result.final_text)
                self.s.ledger.add(result.record.cost_usd)

                # Killed by the user → roll back the worktree clean, requeue (§7).
                if result.record.terminal_reason == KILLED_USER:
                    await self.s.worktrees.rollback_and_remove(wt)
                    self.s.store.update_issue_status(slug, issue.id, IssueStatus.QUEUED)
                    if self.s.monitor:
                        self.s.monitor.worker_finished(issue.id, "killed", result)
                    return "killed"

                # Turn-budget extension: a worker that asks for more turns (or one cut
                # off by the turn limit) gets a bounded resume of the SAME session to
                # continue, instead of escalating. Only turn exhaustion / an explicit
                # request — cost/timeout/stuck kills still escalate below.
                summary = result.summary
                wants_more = bool(summary and summary.request_more_turns
                                  and not summary.escalate)
                hard_turns = result.record.terminal_reason == KILLED_TURNS
                if should_extend(
                    result.record.terminal_reason,
                    has_session=bool(prior_session_id),
                    extensions=turn_extensions,
                    max_extensions=self.s.config.max_turn_extensions,
                    auto_extend=self.s.config.auto_extend_turns,
                    requested_more=wants_more,
                ):
                    turn_extensions += 1
                    self.s._log(
                        f"  ↻ {issue.id}: turn extension "
                        f"{turn_extensions}/{self.s.config.max_turn_extensions} "
                        f"({'requested' if wants_more else 'cut off'}) — "
                        "resuming the same session"
                    )
                    continue  # keep the worktree; do NOT touch `attempts`
                if (wants_more or hard_turns) and self.s.config.auto_extend_turns:
                    # Wanted to extend but no resumable session / extension cap reached.
                    reason = (
                        f"turn budget exhausted after {turn_extensions} extension(s)"
                        if prior_session_id else
                        "turn budget exhausted (no resumable session to continue)"
                    )
                    self.s._escalate(slug, issue, reason)
                    self.s._stamp_outcome(slug, result, metrics_mod.escalated(reason))
                    await self.s.worktrees.remove(wt)
                    if self.s.monitor:
                        self.s.monitor.worker_finished(issue.id, "needs_human", result)
                    return "escalated"

                # Agent asked for help, or a budget/timeout/stuck kill → escalate.
                esc = result.escalation_reason
                if esc:
                    self.s._escalate(slug, issue, esc)
                    self.s._stamp_outcome(slug, result, metrics_mod.escalated(esc))
                    await self.s.worktrees.remove(wt)
                    if self.s.monitor:
                        self.s.monitor.worker_finished(issue.id, "needs_human", result)
                    return "escalated"

                # WS3.2: mandatory handoff. A completion claim without an updated
                # progress.md is structurally rejected and counts as a failed attempt.
                prior_progress = self.s.store.read_run_progress(slug, run_id)
                if not prior_progress.strip():
                    attempts = issue.attempts + 1
                    self.s.store.update_issue_status(
                        slug, issue.id, IssueStatus.TESTS_FAILING, attempts=attempts
                    )
                    failure_report = distiller.distill(
                        attempt=attempts, reason="no progress.md handoff was written",
                        failing_output=(
                            "You ended without updating progress.md in your run dir "
                            "(what was done / what remains / dead ends tried / next step). "
                            "The handoff is mandatory — write it before you stop."
                        ),
                        summary=result.summary,
                    )
                    if attempts >= self.s.config.limits.max_retries:
                        self.s._escalate(slug, issue,
                                         "repeatedly finished without a progress.md handoff")
                        self.s._stamp_outcome(slug, result,
                                              metrics_mod.escalated("no progress.md handoff"))
                        await self.s.worktrees.remove(wt)
                        if self.s.monitor:
                            self.s.monitor.worker_finished(issue.id, "needs_human", result)
                        return "escalated"
                    continue

                # The trust boundary, collapsed into ONE verdict (WS1/WS2): evidence
                # + acceptance + suite + ratchet + the read-only evaluator. The
                # scheduler owns the side effects; merge_gate.decide owns the policy.
                summary_evidence = result.summary.evidence if result.summary else []

                async def _on_structural_pass():
                    # Commit so the slice is reviewable by the evaluator and mergeable;
                    # awaited BEFORE the evaluator diffs the worktree.
                    await git_ops.commit_all(wt, f"{issue.id}: {issue.title}")

                async def _evaluate_cb(g):
                    # WS2: the builder never grades its own work — spawn the read-only
                    # evaluator from a fresh context.
                    self.s.store.update_issue_status(
                        slug, issue.id, IssueStatus.AWAITING_EVALUATION
                    )
                    return await self.s._evaluate(slug, issue, wt, g, evidence_dir)

                async def _code_review_cb(_g):
                    # WS7: read-only code-review gate agent on the committed slice.
                    return await self.s._review(slug, issue, wt)

                async def _security_cb(_g):
                    # WS7: read-only security-review gate agent on the committed slice.
                    return await self.s._security(slug, issue, wt)

                def _distill(attempt, reason, failing_output):
                    return distiller.distill(
                        attempt=attempt, reason=reason,
                        failing_output=failing_output, summary=result.summary,
                    )

                decision = await merge_gate.decide(
                    issue=issue, worktree=wt, commands=commands,
                    check_dir=self.s.store.paths.issue_check_dir(slug, issue.id),
                    evidence_dir=evidence_dir,
                    baseline_path=self.s.store.paths.baseline_file(slug),
                    summary_evidence=summary_evidence,
                    env=hookinst.env, timeout_s=self.s.verify_timeout_s,
                    attempts=issue.attempts, max_retries=self.s.config.limits.max_retries,
                    eval_bounces=eval_bounces,
                    evaluator_enabled=self.s.config.evaluator_enabled,
                    on_structural_pass=_on_structural_pass,
                    evaluate=_evaluate_cb, distill=_distill,
                    summary_claims_pass=(result.summary.claims_pass
                                         if result.summary else None),
                    # WS7: extra read-only gate agents (opt-in via config).
                    code_review_enabled=self.s.config.code_review_enabled,
                    code_review=_code_review_cb,
                    security_review_enabled=self.s.config.security_review_enabled,
                    security_review=_security_cb,
                )
                gate = decision.gate

                if decision.action is merge_gate.Action.MERGE:
                    self.s.store.mark_issue_passed(
                        slug, issue.id, evidence=gate.evidence_artifacts
                    )
                    status = await self.s._land(slug, issue, branch, gate, wt)
                    self.s.store.update_issue_status(slug, issue.id, status)
                    self.s._stamp_outcome(  # WS6: success_first_try | success_after_retry(n)
                        slug, result, metrics_mod.label_success(issue.attempts + 1))
                    await self.s.worktrees.remove(wt)
                    if self.s.monitor:
                        self.s.monitor.worker_finished(issue.id, status.value, result)
                    return "done"

                if decision.action is merge_gate.Action.ESCALATE:
                    self.s._escalate(slug, issue, decision.reason)
                    self.s._stamp_outcome(
                        slug, result, metrics_mod.escalated(decision.outcome))
                    await self.s.worktrees.remove(wt)
                    if self.s.monitor:
                        self.s.monitor.worker_finished(issue.id, "needs_human", result)
                    return "escalated"

                # BOUNCE — retry a FRESH session with the distilled failure report,
                # keeping the worktree so the agent iterates.
                attempts = issue.attempts + 1
                self.s.store.update_issue_status(
                    slug, issue.id, IssueStatus.TESTS_FAILING, attempts=attempts
                )
                failure_report = decision.report
                if decision.is_evaluator_bounce:
                    eval_bounces += 1
                    self.s._stamp_outcome(slug, result, metrics_mod.evaluator_bounce())
        finally:
            self.s._cancels.pop(issue.id, None)
            hooks.cleanup(wt)
            locks.release(integ, issue.id)  # WS4.2
