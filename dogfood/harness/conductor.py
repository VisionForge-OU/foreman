"""The conductor: drives one feature end-to-end through the REAL Foreman TUI via
Textual Pilot, playing the synthetic reviewer at every gate, and asserting every
transition against on-disk state. Identical for the mock dry-run and the real run
(only the app construction + judge differ).

Design notes
------------
* Every gate action is enacted through a widget/keypress (``pilot.press`` /
  setting ``TextArea.text`` then submitting) — operating the real gate widgets is
  the coverage the goal asks for.
* Synchronization is disk-truth: after each action we ``wait_for`` a predicate
  over ``.foreman/`` state, not over widget internals.
* Anything that goes wrong (timeout, crash, state-vs-display mismatch) is appended
  to ``self.findings`` as a structured record and the conductor continues, rather
  than hanging or aborting the campaign.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from textual.widgets import Button, Input, TextArea

from foreman.models import DocStatus, Phase

from . import snapshots, state_reader
from .wait_for import WaitTimeout, wait_for, wait_until_idle


@dataclass
class Timeouts:
    phase: float = 90.0       # plan/grill/slice agent runs
    build: float = 240.0      # one build round (may host several workers)
    gate: float = 10.0        # disk reflects an approve/confirm
    review_cycles: int = 2    # max request-changes→revise loops per gate
    build_rounds: int = 6     # max build/escalation rounds
    rescue_each: float = 240.0


@dataclass
class FeatureSpec:
    key: str                  # f1..f5
    ftype: str                # greenfield | brownfield | multi | vague | trivial
    title: str
    request: str
    force_rc_gate: str = ""   # "" | "plan" | "prd" | "queue" — mandate one RC cycle here
    force_max_retries: int | None = None  # lower to force escalation (mock rescue demo)


class Conductor:
    def __init__(self, *, reviewer, deliverables, guardrails, snap_dir: Path,
                 clock_start: float, timeouts: Timeouts | None = None, real: bool,
                 worker_model: str = "mock"):
        self.reviewer = reviewer
        self.dv = deliverables
        self.guard = guardrails
        self.snap_dir = Path(snap_dir)
        self.clock_start = clock_start
        self.t = timeouts or Timeouts()
        self.real = real
        self.worker_model = worker_model
        self.findings: list[dict] = []
        self.harness_spend = 0.0

    # ------------------------------------------------------------------ #
    # Findings + guardrail
    # ------------------------------------------------------------------ #
    def finding(self, *, severity: str, area: str, title: str, detail: str = "",
                feature: str = "") -> None:
        self.findings.append({"severity": severity, "area": area, "title": title,
                              "detail": detail, "feature": feature})

    def _bill_judge(self, feature: str, gate: str, cost: float) -> None:
        """Account a synthetic-reviewer LLM-judge call (harness-side spend)."""
        if cost <= 0:
            return
        self.harness_spend += cost
        self.dv.append_cost(feature=feature, label=f"judge:{gate}", source="harness-judge",
                            model="claude-haiku-4-5-20251001", real=self.real,
                            cost_usd=cost, note="auto-reviewer judgment")

    def _elapsed(self) -> float:
        return time.monotonic() - self.clock_start

    def ceiling_hit(self, store) -> bool:
        from .guardrails import foreman_spend
        scratch_root = _scratch_of(store)
        s = self.guard.status(
            foreman_spend_usd=foreman_spend(scratch_root),
            harness_spend_usd=self.dv.harness_spend() + self.harness_spend,
            elapsed_s=self._elapsed())
        if s.level == "warn":
            self.finding(severity="minor", area="guardrail",
                         title=f"guardrail warning: {s.reason}",
                         detail=f"spent=${s.spent_usd:.2f} elapsed={s.elapsed_s:.0f}s")
        return s.should_stop

    # ------------------------------------------------------------------ #
    # Low-level TUI helpers
    # ------------------------------------------------------------------ #
    async def _press(self, pilot, *keys) -> None:
        await pilot.press(*keys)
        await pilot.pause()

    async def _to_dashboard(self, pilot, app) -> None:
        # Pop any pushed screens back to the dashboard.
        for _ in range(5):
            if type(app.screen).__name__ == "DashboardScreen":
                return
            await self._press(pilot, "escape")
        if type(app.screen).__name__ != "DashboardScreen":
            self.finding(severity="major", area="tui",
                         title="could not return to dashboard",
                         detail=f"stuck on {type(app.screen).__name__}")

    def _snap(self, app, label: str) -> dict:
        return snapshots.capture(app, self.snap_dir, label)

    def _check_state_vs_display(self, app, store, slug, label: str) -> None:
        """Disk-truth phase vs what the dashboard hint shows (goal: mismatch checks)."""
        try:
            disk_phase = store.load_feature(slug).phase.value
            screen = app.screen
            if type(screen).__name__ != "DashboardScreen":
                return
            hint = str(screen.query_one("#hint").renderable)
            if disk_phase not in hint:
                self.finding(severity="minor", area="tui",
                             title="state-vs-display mismatch",
                             detail=f"[{label}] disk phase={disk_phase!r} not reflected in "
                                    f"dashboard hint: {hint[:120]!r}", feature=slug)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Top-level sequencer
    # ------------------------------------------------------------------ #
    async def drive_feature(self, app, pilot, fspec: FeatureSpec) -> dict:
        """Walk one feature through every gate. Returns a debrief dict."""
        store = app.controller.store
        t0 = time.monotonic()
        debrief = {"key": fspec.key, "type": fspec.ftype, "title": fspec.title,
                   "outcome": "incomplete", "stages": {}, "slug": None}

        slug = await self.submit_request(app, pilot, fspec)
        debrief["slug"] = slug
        self._snap(app, f"{slug}-00-request")
        if not slug:
            debrief["outcome"] = "failed:request"
            return debrief

        stages = [
            ("plan", lambda: self.run_phase(app, pilot, "p", "planner", slug,
                                            state_reader.plan_drafted, self.t.phase)),
            ("plan_review", lambda: self.review_gate(
                app, pilot, slug, ("plan",), fspec.request, fspec, "p", "planner",
                state_reader.plan_drafted)),
            ("grill", lambda: self.run_phase(app, pilot, "g", "grill", slug,
                                             state_reader.docs_drafted, self.t.phase)),
            ("doc_review", lambda: self.review_gate(
                app, pilot, slug, ("adr", "prd"), fspec.request, fspec, "g", "grill",
                state_reader.docs_drafted)),
            ("slice", lambda: self.run_phase(app, pilot, "s", "slicer", slug,
                                             state_reader.issues_sliced, self.t.phase)),
            ("queue", lambda: self.confirm_queue(app, pilot, slug, fspec)),
        ]
        for name, fn in stages:
            if self.ceiling_hit(store):
                debrief["outcome"] = "ceiling"
                return debrief
            ok = await fn()
            debrief["stages"][name] = bool(ok)
            if not ok:
                debrief["outcome"] = f"failed:{name}"
                return debrief

        build_result = await self.drive_build(app, pilot, slug, fspec)
        debrief["stages"]["build"] = build_result
        debrief["outcome"] = ("done" if build_result == "done" else f"build:{build_result}")
        debrief["wall_s"] = round(time.monotonic() - t0, 1)
        debrief["cost_usd"] = round(app.controller.feature_cost(slug), 4)
        debrief["issue_counts"] = state_reader.issue_status_counts(store, slug)
        try:
            debrief["metrics_pane"] = app.controller.feature_metrics_text(slug)
        except Exception as e:
            debrief["metrics_pane"] = f"(metrics error: {e})"
        self._snap(app, f"{slug}-99-final")
        return debrief

    # ------------------------------------------------------------------ #
    # Gates
    # ------------------------------------------------------------------ #
    async def submit_request(self, app, pilot, fspec: FeatureSpec) -> str:
        await self._to_dashboard(pilot, app)
        await self._press(pilot, "n")
        screen = app.screen
        if type(screen).__name__ != "NewFeatureScreen":
            # Fallback: create directly via controller (still disk-authoritative).
            self.finding(severity="major", area="tui",
                         title="New-feature modal did not open on 'n'",
                         detail=f"got {type(screen).__name__}")
            slug = app.controller.create_feature(fspec.title, fspec.request)
            app.current_slug = slug
            await self._to_dashboard(pilot, app)
            return slug
        screen.query_one("#title", Input).value = fspec.title
        screen.query_one("#req", TextArea).text = fspec.request
        await pilot.pause()
        try:
            await pilot.click("#create")
        except Exception:
            screen.query_one("#create", Button).press()
        await pilot.pause()
        slug = app.current_slug
        if not slug:
            self.finding(severity="major", area="tui",
                         title="feature creation did not set current_slug")
        return slug

    async def run_phase(self, app, pilot, key: str, label: str, slug: str,
                        ready_pred, timeout: float) -> bool:
        await self._to_dashboard(pilot, app)
        t0 = time.monotonic()
        await self._press(pilot, key)
        store = app.controller.store
        try:
            await wait_until_idle(pilot, app.controller, timeout=timeout, desc=f"{label} idle")
            await wait_for(pilot, lambda: ready_pred(store, slug), timeout=self.t.gate,
                           desc=f"{label} disk-ready")
        except WaitTimeout as e:
            self._snap(app, f"{slug}-{label}-TIMEOUT")
            self.finding(severity="major", area="pipeline",
                         title=f"{label} timed out", detail=str(e), feature=slug)
            return False
        self.dv.record_transition(feature=slug, transition=f"{label}",
                                  latency_s=time.monotonic() - t0)
        await self._to_dashboard(pilot, app)
        self._check_state_vs_display(app, store, slug, label)
        return True

    async def review_gate(self, app, pilot, slug: str, kinds: tuple[str, ...],
                          request: str, fspec: FeatureSpec, revise_key: str,
                          revise_label: str, revise_ready) -> bool:
        """Drive ReviewScreen over the given doc kinds, looping revise cycles until
        all approved or the cycle cap is hit. Returns True if all kinds approved."""
        store = app.controller.store
        for cycle in range(self.t.review_cycles + 1):
            decisions = {}
            for kind in kinds:
                doc = store.load_feature(slug).doc(kind)
                if doc is None:
                    continue
                decision = await self._decide_doc(app, slug, kind, doc, request, fspec)
                decisions[kind] = decision
            # Enact: if ANY needs changes, request changes on those (approve none yet).
            need_revise = any(d.action == "request_changes" for d in decisions.values())
            await self._open_review(app, pilot, slug)
            if need_revise:
                for kind, d in decisions.items():
                    if d.action == "request_changes":
                        await self._enact_review(app, pilot, slug, kind, d)
                await self._to_dashboard(pilot, app)
                if cycle >= self.t.review_cycles:
                    self.finding(severity="major", area="pipeline",
                                 title=f"{slug} {kinds} not approvable within "
                                       f"{self.t.review_cycles} cycles", feature=slug)
                    return False
                # Re-run the producer to revise.
                ok = await self.run_phase(app, pilot, revise_key, revise_label, slug,
                                          revise_ready, self.t.phase)
                if not ok:
                    return False
                continue
            # All approve.
            for kind, d in decisions.items():
                await self._enact_review(app, pilot, slug, kind, d)
            await self._to_dashboard(pilot, app)
            # Verify disk reflects approval.
            try:
                await wait_for(pilot,
                               lambda: all(state_reader.doc_approved(store, slug, k)
                                           for k in kinds if store.load_feature(slug).doc(k)),
                               timeout=self.t.gate, desc=f"{kinds} approved on disk")
            except WaitTimeout as e:
                self.finding(severity="major", area="gate",
                             title=f"approval not persisted for {kinds}", detail=str(e),
                             feature=slug)
                return False
            return True
        return False

    async def _decide_doc(self, app, slug, kind, doc, request, fspec):
        oq = doc.open_questions
        force = (fspec.force_rc_gate == kind)
        summary = f"{kind} v{doc.version} ({len(doc.body)} chars, {len(oq)} open Q)"
        decision = await self.reviewer.review(
            gate=kind, slug=slug, request=request, body=doc.body, summary=summary,
            open_questions=oq, structural_problems=[])
        if force and decision.action == "approve":
            # Honor mandated coverage even if the judge would approve.
            decision.action = "request_changes"
            decision.comments = decision.comments or "Tighten and resubmit (mandated cycle)."
            decision.rationale = "[mandatory coverage] " + decision.rationale
        self._bill_judge(slug, kind, decision.judge_cost_usd)
        self.dv.log_autoreview(feature=slug, gate=kind, decision=decision.action,
                               draft_summary=summary, scores=decision.scores,
                               rationale=decision.rationale,
                               action_detail=("approve 'a'" if decision.action == "approve"
                                              else "type #comments + 'r'"))
        return decision

    async def _open_review(self, app, pilot, slug) -> None:
        await self._to_dashboard(pilot, app)
        await self._press(pilot, "v")
        if type(app.screen).__name__ != "ReviewScreen":
            self.finding(severity="major", area="tui", title="ReviewScreen did not open on 'v'",
                         feature=slug)

    async def _enact_review(self, app, pilot, slug, kind, decision) -> None:
        screen = app.screen
        if type(screen).__name__ != "ReviewScreen":
            await self._open_review(app, pilot, slug)
            screen = app.screen
        # Cycle to the target kind.
        for _ in range(len(("plan", "adr", "prd")) + 1):
            if getattr(screen, "kind", None) == kind:
                break
            screen.action_cycle_doc()
            await pilot.pause()
        self._snap(app, f"{slug}-review-{kind}-{decision.action}")
        if decision.action == "approve":
            await self._press(pilot, "a")
        else:
            screen.query_one("#comments", TextArea).text = decision.comments
            await pilot.pause()
            await self._press(pilot, "r")

    async def confirm_queue(self, app, pilot, slug: str, fspec: FeatureSpec) -> bool:
        store = app.controller.store
        for cycle in range(self.t.review_cycles + 1):
            st = store.load_feature(slug)
            issues = [i for i in st.issues if not i.is_janitor]
            problems = []
            for i in issues:
                if not i.acceptance_check.strip():
                    problems.append(f"{i.id} missing acceptance_check")
                if not i.touches:
                    problems.append(f"{i.id} missing touches (footprint)")
                if not i.prd_refs:
                    problems.append(f"{i.id} missing prd_refs (traceability)")
            summary = (f"{len(issues)} issues: " +
                       ", ".join(f"{i.id}({','.join(i.depends_on) or 'no-deps'})" for i in issues))
            force = (fspec.force_rc_gate == "queue")
            decision = await self.reviewer.review(
                gate="queue", slug=slug, request=fspec.request,
                body=app.controller.queue_review_text(slug), summary=summary,
                open_questions=[], structural_problems=problems)
            if force and decision.action == "approve" and cycle == 0:
                decision.action = "request_changes"
                decision.comments = "Re-slice with sharper acceptance checks (mandated cycle)."
                decision.rationale = "[mandatory coverage] " + decision.rationale
            self._bill_judge(slug, "queue", decision.judge_cost_usd)
            self.dv.log_autoreview(feature=slug, gate="queue", decision=decision.action,
                                   draft_summary=summary, scores=decision.scores,
                                   rationale=decision.rationale,
                                   action_detail="confirm 'c'" if decision.action == "approve"
                                   else "re-run slicer")
            self._snap(app, f"{slug}-queue-{decision.action}")
            if decision.action == "approve":
                await self._to_dashboard(pilot, app)
                await self._press(pilot, "c")
                try:
                    await wait_for(pilot, lambda: state_reader.queue_confirmed(store, slug),
                                   timeout=self.t.gate, desc="queue confirmed")
                except WaitTimeout as e:
                    self.finding(severity="major", area="gate", title="queue confirm not persisted",
                                 detail=str(e), feature=slug)
                    return False
                self.dv.record_transition(feature=slug, transition="queue_confirmed", latency_s=0)
                return True
            # request changes → re-slice
            if cycle >= self.t.review_cycles:
                self.finding(severity="major", area="pipeline",
                             title=f"{slug} queue not confirmable within cycles", feature=slug)
                return False
            ok = await self.run_phase(app, pilot, "s", "slicer", slug,
                                      state_reader.issues_sliced, self.t.phase)
            if not ok:
                return False
        return False

    async def drive_build(self, app, pilot, slug: str, fspec: FeatureSpec) -> str:
        """Run the build, rescuing escalations through the attention queue. Returns
        'done' | 'escalated_unrescued' | 'stuck' | 'ceiling'."""
        store = app.controller.store
        if fspec.force_max_retries is not None:
            app.controller.config.limits.max_retries = fspec.force_max_retries
        rescued_any = False
        for rnd in range(self.t.build_rounds):
            if self.ceiling_hit(store):
                return "ceiling"
            t0 = time.monotonic()
            await self._to_dashboard(pilot, app)
            await self._press(pilot, "b")

            # Disk-truth terminal detection (the controller leaves some WorkerLog
            # entries stuck at "running" after completion — see findings — so we do
            # NOT trust the in-memory activity/worker flags here).
            def terminal() -> bool:
                return (state_reader.feature_done(store, slug)
                        or bool(state_reader.open_escalations(store, slug)))
            try:
                await wait_for(pilot, terminal, timeout=self.t.build,
                               desc="build round terminal (done|escalation)")
            except WaitTimeout as e:
                self._snap(app, f"{slug}-build-TIMEOUT-r{rnd}")
                self.finding(severity="major", area="pipeline", title="build round timed out",
                             detail=str(e) + " counts=" +
                             str(state_reader.issue_status_counts(store, slug)), feature=slug)
                return "stuck"
            self.dv.record_transition(feature=slug, transition=f"build_round_{rnd}",
                                      latency_s=time.monotonic() - t0,
                                      detail=str(state_reader.issue_status_counts(store, slug)))
            self._reconcile_costs(app, slug)
            if state_reader.feature_done(store, slug):
                stale = [k for k, w in app.controller.workers.items() if w.status == "running"]
                if stale:
                    self.finding(severity="minor", area="tui",
                                 title="worker(s) stuck at status='running' after feature done",
                                 detail=f"controller.workers never finalized: {stale} "
                                        "(WorkerScreen would show them perpetually running)",
                                 feature=slug)
                return "done"
            escs = state_reader.open_escalations(store, slug)
            if escs:
                for iid in escs:
                    await self._rescue(app, pilot, slug, iid, fspec)
                    rescued_any = True
                if state_reader.feature_done(store, slug):
                    return "done"
                continue
            counts = state_reader.issue_status_counts(store, slug)
            self.finding(severity="major", area="pipeline",
                         title="build terminal but feature not done and no escalation",
                         detail=str(counts), feature=slug)
            return "stuck"
        return "escalated_unrescued" if not rescued_any else "stuck"

    async def _rescue(self, app, pilot, slug, iid, fspec) -> None:
        store = app.controller.store
        reason = app.controller.escalation_text(slug, iid)
        self._snap(app, f"{slug}-escalation-{iid}")
        answer, cost = await self.reviewer.answer_escalation(
            reason=reason, request=fspec.request, context=f"issue {iid}")
        self._bill_judge(slug, f"escalation:{iid}", cost)
        self.dv.log_autoreview(feature=slug, gate=f"escalation:{iid}", decision="answer",
                               draft_summary=f"escalation: {reason[:140]}", scores={},
                               rationale="escalation clarity logged; answered substantively",
                               action_detail=f"AttentionScreen → #answer + Ctrl+S: {answer[:120]}")
        await self._to_dashboard(pilot, app)
        await self._press(pilot, "x")
        screen = app.screen
        if type(screen).__name__ != "AttentionScreen":
            self.finding(severity="major", area="tui", title="AttentionScreen did not open on 'x'",
                         feature=slug)
            return
        # Select the escalation if needed.
        if screen.selected != iid:
            screen.selected = iid
        screen.query_one("#answer", TextArea).text = answer
        await pilot.pause()
        await self._press(pilot, "ctrl+s")

        # Disk-truth: the resume writes a `## Answer` (so the escalation is no longer
        # "open"), then re-runs the issue. We're done when the issue reaches a
        # terminal state, OR it re-escalates (a fresh `## Escalation` after the
        # answer), OR the whole feature completes.
        from foreman.models import IssueStatus
        terminal_states = (IssueStatus.MERGED, IssueStatus.DONE)

        def resume_done() -> bool:
            st = store.load_feature(slug)
            iss = st.issue(iid)
            if iss and iss.status in terminal_states:
                return True
            if st.phase.value == "done":
                return True
            txt = store.read_escalation(slug, iid)
            return state_reader.escalation_open(txt)  # re-escalated
        try:
            await wait_for(pilot, resume_done, timeout=self.t.rescue_each,
                           desc=f"resume {iid} terminal")
        except WaitTimeout as e:
            self.finding(severity="major", area="pipeline", title=f"rescue of {iid} did not resolve",
                         detail=str(e), feature=slug)
        await self._to_dashboard(pilot, app)

    def _reconcile_costs(self, app, slug) -> None:
        """Append any new foreman run costs to the human-facing ledger (idempotent)."""
        store = app.controller.store
        recorded = self.dv.recorded_run_ids()
        feat_dir = Path(store.paths.feature_dir(slug))
        import json
        for usage in sorted(feat_dir.glob("runs/*/usage.json")):
            try:
                data = json.loads(usage.read_text())
            except (ValueError, OSError):
                continue
            rid = data.get("run_id") or usage.parent.name
            if rid in recorded:
                continue
            self.dv.append_cost(feature=slug, label=data.get("label", ""),
                                source="foreman-worker", model=self.worker_model,
                                real=self.real, cost_usd=float(data.get("cost_usd", 0.0) or 0.0),
                                turns=int(data.get("num_turns", 0) or 0),
                                note=data.get("outcome", "") or data.get("terminal_reason", ""),
                                run_id=rid)


def _scratch_of(store) -> Path:
    """Repo root for a FileStore (parent of .foreman)."""
    return Path(store.paths.daily_cost_file).parent.parent
