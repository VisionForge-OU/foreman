import itertools

import pytest

from foreman.backend import MockBackend
from foreman.config import Config
from foreman.demo_scripts import demo_scripts
from foreman.installer import init_repo
from foreman.ledger import CostLedger
from foreman.models import IssueStatus
from foreman.sample import create_sample_repo, pytest_command
from foreman.scheduler import Scheduler, SchedulerError
from foreman.state import FileStore


def _config(daily=50.0, max_retries=3, max_parallel=2):
    cfg = Config()
    cfg.commands = {"test": pytest_command(), "lint": "", "typecheck": "", "e2e": ""}
    cfg.limits.daily_cost_usd = daily
    cfg.limits.max_retries = max_retries
    cfg.limits.max_parallel = max_parallel
    cfg.e2e_enabled = False  # exercised separately; keep these tests fast
    cfg.stuck_turns = 0
    return cfg


async def _prepare_feature(tmp_path, *, scripts=None):
    """A sample repo with an approved PRD, confirmed queue, and two issues."""
    repo = create_sample_repo(tmp_path / "repo")
    init_repo(repo)
    counter = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")

    # Drive Phase A with the pipeline to land real issues on disk.
    from foreman.pipeline import Pipeline
    rc = itertools.count(1)
    pipe = Pipeline(store, _config(), MockBackend(demo_scripts()),
                    run_id_clock=lambda: f"p{next(rc):04d}")
    slug = store.create_feature("todo done", "Add a done command")
    await _phase_a(pipe, store, slug)
    return repo, store, slug


async def _phase_a(pipe, store, slug):
    await pipe.run_planner(slug)
    store.approve_doc(slug, "plan", "arash")
    await pipe.run_grill(slug)
    store.request_changes(slug, "prd", "arash", "no-op please")
    await pipe.run_grill(slug)
    store.approve_doc(slug, "prd", "arash")
    store.approve_doc(slug, "adr", "arash")
    await pipe.run_slicer(slug)
    store.confirm_queue(slug)


def _scheduler(store, cfg, scripts=None):
    rc = itertools.count(1)
    backend = MockBackend(scripts or demo_scripts())
    return Scheduler(store, cfg, backend,
                     ledger=CostLedger(store.paths.daily_cost_file),
                     run_id_clock=lambda: f"s{next(rc):04d}")


@pytest.mark.asyncio
async def test_build_completes_both_issues(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    sched = _scheduler(store, _config())
    report = await sched.build(slug)
    state = store.load_feature(slug)
    statuses = {i.id: i.status for i in state.issues}
    assert statuses["ISS-001"] == IssueStatus.MERGED
    assert statuses["ISS-002"] == IssueStatus.MERGED
    assert set(report.merged) == {"ISS-001", "ISS-002"}
    assert report.total_cost_usd > 0


@pytest.mark.asyncio
async def test_build_requires_queue_confirmation(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    store.unconfirm_queue(slug)
    sched = _scheduler(store, _config())
    with pytest.raises(SchedulerError):
        await sched.build(slug)


@pytest.mark.asyncio
async def test_failing_then_passing_retry(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    # ISS-001 fails its first attempt (Foreman's pytest catches the broken test).
    scripts = demo_scripts(fail_first_issue="ISS-001")
    sched = _scheduler(store, _config(), scripts=scripts)
    report = await sched.build(slug)
    iss1 = store.load_issue(slug, "ISS-001")
    assert iss1.status == IssueStatus.MERGED
    assert iss1.attempts >= 1   # retried at least once
    assert "ISS-001" in report.merged


@pytest.mark.asyncio
async def test_exhausted_retries_escalate(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    cfg = _config(max_retries=1)
    # A script that always writes a broken test -> never passes verification.
    from foreman.demo_scripts import make_tdd_script

    def always_fail(spec):
        # fail_first writes broken on first; force "first" every time by not
        # signalling a retry. We wrap to always look like a fresh attempt.
        return make_tdd_script(fail_first=True)(spec)

    scripts = demo_scripts()
    scripts["tdd:ISS-001"] = always_fail
    sched = _scheduler(store, cfg, scripts=scripts)
    report = await sched.build(slug)
    iss1 = store.load_issue(slug, "ISS-001")
    assert iss1.status == IssueStatus.NEEDS_HUMAN
    assert any(iid == "ISS-001" for iid, _ in report.escalated)
    # Escalation file written for the attention queue.
    assert store.paths.escalation_file(slug, "ISS-001").exists()
    # ISS-002 depends on ISS-001 -> never ran -> blocked.
    assert store.load_issue(slug, "ISS-002").status == IssueStatus.QUEUED


@pytest.mark.asyncio
async def test_agent_escalation_request(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    from foreman.demo_scripts import _init, _result
    from foreman.stream_parser import parse_event

    async def escalating(spec):
        yield _init(spec)
        block = ('```json\n{"schema":"foreman-summary/v1","issue_id":"ISS-001",'
                 '"escalate":true,"escalation_question":"Which storage backend?",'
                 '"commands":{}}\n```')
        yield parse_event({"type": "assistant", "message": {"content": [
            {"type": "text", "text": block}], "usage": {"input_tokens": 1}}})
        yield _result()

    scripts = demo_scripts()
    scripts["tdd:ISS-001"] = escalating
    sched = _scheduler(store, _config(), scripts=scripts)
    await sched.build(slug)
    assert store.load_issue(slug, "ISS-001").status == IssueStatus.NEEDS_HUMAN
    escs = sched.escalations(slug)
    assert any("ISS-001" == iid for iid, _ in escs)


@pytest.mark.asyncio
async def test_resume_after_escalation(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    cfg = _config(max_retries=1)
    scripts = demo_scripts()
    scripts["tdd:ISS-001"] = lambda spec: __import__(
        "foreman.demo_scripts", fromlist=["make_tdd_script"]
    ).make_tdd_script(fail_first=True)(spec)
    sched = _scheduler(store, cfg, scripts=scripts)
    await sched.build(slug)
    assert store.load_issue(slug, "ISS-001").status == IssueStatus.NEEDS_HUMAN

    # Human answers; resume re-runs with a clean (passing) script.
    sched.backend.scripts["tdd:ISS-001"] = demo_scripts()["tdd"]
    outcome = await sched.resume_issue(slug, "ISS-001", "Use the in-memory store.")
    assert outcome == "done"
    assert store.load_issue(slug, "ISS-001").status == IssueStatus.MERGED
    assert "Reviewer answer" in store.paths.escalation_file(slug, "ISS-001").read_text()


@pytest.mark.asyncio
async def test_two_independent_issues_run_in_parallel(tmp_path):
    import asyncio
    from foreman.models import Budget, Issue
    from foreman.demo_scripts import (
        _init, _result, _summary_block, WORKER_CODE, _write_worker_code, _write_evidence,
        _write_progress,
    )
    from foreman.stream_parser import parse_event

    repo, store, slug = await _prepare_feature(tmp_path)
    # Replace the (dependent) sliced issues with two INDEPENDENT ones.
    for i in store.load_feature(slug).issues:
        store.delete_issue(slug, i.id)
    for iid in ("ISS-001", "ISS-002"):
        slot = iid.lower().replace("-", "_")
        body = "## Goal\nx\n## Acceptance criteria (testable)\n- [ ] works\n"
        store.write_issue(slug, Issue(id=iid, title=iid, depends_on=[],
                                      branch=f"feature/{slug}/{iid.lower()}",
                                      budget=Budget(), prd_refs=["PRD §1"], body=body,
                                      acceptance_check=f"tests/test_{slot}.py",
                                      touches=[f"todo/{slot}.py", f"tests/test_{slot}.py"]))

    # A barrier that only releases once BOTH workers have arrived — proving they
    # are genuinely in flight at the same time (would time out if serialized).
    barrier = asyncio.Barrier(2)

    async def barrier_script(spec):
        yield _init(spec)
        await asyncio.wait_for(barrier.wait(), timeout=5)
        # Self-contained code per issue, so the two are truly independent.
        slot = spec.label.lower().replace("-", "_")
        files = {
            f"todo/{slot}.py": f"def value_{slot}():\n    return '{spec.label}'\n",
            f"tests/test_{slot}.py":
                f"from todo.{slot} import value_{slot}\n\n"
                f"def test_{slot}():\n    assert value_{slot}() == '{spec.label}'\n",
        }
        written = _write_worker_code(spec, files)
        ev = _write_evidence(spec, passed=True)
        _write_progress(spec)
        yield parse_event({"type": "assistant", "message": {"content": [
            {"type": "text", "text": _summary_block(spec.label, written, True, evidence=ev)}],
            "usage": {"input_tokens": 1}}})
        yield _result()

    scripts = demo_scripts()
    scripts["tdd"] = barrier_script
    sched = _scheduler(store, _config(max_parallel=2), scripts=scripts)
    report = await sched.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}


@pytest.mark.asyncio
async def test_complete_claim_without_evidence_is_a_failed_attempt(tmp_path):
    """WS1.3 acceptance: a 'complete' summary with no evidence bounces/escalates."""
    repo, store, slug = await _prepare_feature(tmp_path)
    cfg = _config(max_retries=1)
    from foreman.demo_scripts import (
        _init, _result, _summary_block, WORKER_CODE, _write_worker_code, _write_progress,
    )
    from foreman.stream_parser import parse_event

    async def no_evidence(spec):
        yield _init(spec)
        written = _write_worker_code(spec, dict(WORKER_CODE.get(spec.label, {})))
        _write_progress(spec)  # handoff present, so it reaches the evidence gate
        # Code + a passing-claim summary, but it saves NO evidence artifacts.
        yield parse_event({"type": "assistant", "message": {"content": [
            {"type": "text", "text": _summary_block(spec.label, written, True, evidence=[])}],
            "usage": {"input_tokens": 1}}})
        yield _result()

    scripts = demo_scripts()
    scripts["tdd:ISS-001"] = no_evidence
    sched = _scheduler(store, cfg, scripts=scripts)
    await sched.build(slug)
    iss1 = store.load_issue(slug, "ISS-001")
    assert iss1.status == IssueStatus.NEEDS_HUMAN
    assert not store.issue_verified(slug, "ISS-001")  # never flipped to passing
    detail = store.paths.escalation_file(slug, "ISS-001").read_text().lower()
    assert "evidence" in detail


@pytest.mark.asyncio
async def test_evaluator_objection_bounces_then_passes(tmp_path):
    """WS2.3: an evaluator objection bounces to a fresh builder; a later pass merges."""
    repo, store, slug = await _prepare_feature(tmp_path)
    from foreman.demo_scripts import make_evaluator_script

    calls = {"ISS-001": 0}

    def stateful_eval(spec):
        calls["ISS-001"] += 1
        if calls["ISS-001"] == 1:
            return make_evaluator_script(
                verdict="objections", objections=["test mirrors the implementation"]
            )(spec)
        return make_evaluator_script(verdict="pass")(spec)

    scripts = demo_scripts()
    scripts["evaluator:ISS-001-eval"] = stateful_eval
    sched = _scheduler(store, _config(), scripts=scripts)
    await sched.build(slug)
    iss1 = store.load_issue(slug, "ISS-001")
    assert iss1.status == IssueStatus.MERGED
    assert iss1.attempts >= 1            # the objection cost an attempt
    assert calls["ISS-001"] >= 2         # evaluator ran again after the bounce
    assert store.issue_verified(slug, "ISS-001") is True


@pytest.mark.asyncio
async def test_evaluator_uncertain_escalates(tmp_path):
    """WS2.3: an 'uncertain' verdict escalates to the human, not merge."""
    repo, store, slug = await _prepare_feature(tmp_path)
    from foreman.demo_scripts import make_evaluator_script

    scripts = demo_scripts()
    scripts["evaluator:ISS-001-eval"] = make_evaluator_script(verdict="uncertain")
    sched = _scheduler(store, _config(), scripts=scripts)
    await sched.build(slug)
    iss1 = store.load_issue(slug, "ISS-001")
    assert iss1.status == IssueStatus.NEEDS_HUMAN
    assert not store.issue_verified(slug, "ISS-001")
    assert store.paths.escalation_file(slug, "ISS-001").exists()


@pytest.mark.asyncio
async def test_evaluator_disabled_merges_without_grading(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    cfg = _config()
    cfg.evaluator_enabled = False
    # No evaluator script registered; with grading off it must never be needed.
    scripts = demo_scripts()
    del scripts["evaluator"]
    sched = _scheduler(store, cfg, scripts=scripts)
    report = await sched.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}


@pytest.mark.asyncio
async def test_missing_progress_handoff_is_a_failed_attempt(tmp_path):
    """WS3.2: finishing without updating progress.md is structurally rejected."""
    repo, store, slug = await _prepare_feature(tmp_path)
    cfg = _config(max_retries=1)
    from foreman.demo_scripts import _init, _result, _summary_block, WORKER_CODE, \
        _write_worker_code, _write_evidence
    from foreman.stream_parser import parse_event

    async def no_handoff(spec):
        yield _init(spec)
        written = _write_worker_code(spec, dict(WORKER_CODE.get(spec.label, {})))
        ev = _write_evidence(spec, passed=True)  # evidence present, but NO progress.md
        yield parse_event({"type": "assistant", "message": {"content": [
            {"type": "text", "text": _summary_block(spec.label, written, True, evidence=ev)}],
            "usage": {"input_tokens": 1}}})
        yield _result()

    scripts = demo_scripts()
    scripts["tdd:ISS-001"] = no_handoff
    sched = _scheduler(store, cfg, scripts=scripts)
    await sched.build(slug)
    iss1 = store.load_issue(slug, "ISS-001")
    assert iss1.status == IssueStatus.NEEDS_HUMAN
    assert "progress.md" in store.paths.escalation_file(slug, "ISS-001").read_text()


@pytest.mark.asyncio
async def test_initializer_runs_once_and_seeds_artifacts(tmp_path):
    """WS3.1: the feature initializer runs and seeds init.sh + feature-state.md."""
    repo, store, slug = await _prepare_feature(tmp_path)
    sched = _scheduler(store, _config())
    await sched.build(slug)
    assert store.paths.init_script(slug).exists()
    assert "Conventions" in store.paths.feature_state_file(slug).read_text()
    # A second build does not re-run the initializer (idempotent in-process).
    assert slug in sched._initialized


@pytest.mark.asyncio
async def test_fresh_retry_carries_distilled_report_and_logs_tokens(tmp_path):
    """WS3.3/3.4: a retry gets a distilled failure report; prompt tokens are recorded."""
    import json
    repo, store, slug = await _prepare_feature(tmp_path)
    prompts: list[str] = []
    from foreman.demo_scripts import make_tdd_script

    def capture(spec):
        prompts.append(spec.prompt)
        return make_tdd_script(fail_first=True)(spec)

    scripts = demo_scripts()
    scripts["tdd:ISS-001"] = capture
    sched = _scheduler(store, _config(), scripts=scripts)
    await sched.build(slug)
    # First attempt has no failure report; the retry carries a distilled one.
    assert not any("distilled failure report" in p for p in prompts[:1])
    assert any("distilled failure report" in p for p in prompts[1:])
    # Assembled-prompt token counts are recorded on the run records (WS3.4).
    usages = list(store.paths.runs_dir(slug).glob("*-ISS-001/usage.json"))
    assert any(json.loads(u.read_text()).get("prompt_tokens", 0) > 0 for u in usages)


@pytest.mark.asyncio
async def test_stale_lock_is_reclaimed_and_build_proceeds(tmp_path):
    """WS4.2: a dead worker's stale lock is reclaimed so the build can proceed."""
    from foreman import locks
    repo, store, slug = await _prepare_feature(tmp_path)
    sched = _scheduler(store, _config())
    # Plant a STALE lock for ISS-001 (heartbeat far in the past) before building.
    await sched.worktrees.ensure_base()
    integ = await sched.worktrees.integration_worktree()
    locks.acquire(integ, "ISS-001", run_id="dead-worker", now=0.0, ttl_s=10)
    report = await sched.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}  # reclaimed, then built
    assert locks.active(integ) == {}  # released after completion


@pytest.mark.asyncio
async def test_orphaned_in_progress_issue_recovered_after_restart(tmp_path):
    """R4 crash recovery: an issue left IN_PROGRESS by a dead run (e.g. SIGKILL) is
    requeued and finished on restart, not silently stalled. Its fresh-heartbeat lock
    from the dead worker is dropped too."""
    import time
    from foreman import locks
    repo, store, slug = await _prepare_feature(tmp_path)
    # Simulate a crash mid-ISS-001: status stuck IN_PROGRESS + a dead worker's lock
    # whose heartbeat is recent (so the stale-TTL reclaim alone would NOT free it).
    store.update_issue_status(slug, "ISS-001", IssueStatus.IN_PROGRESS)
    sched = _scheduler(store, _config())
    await sched.worktrees.ensure_base()
    integ = await sched.worktrees.integration_worktree()
    locks.acquire(integ, "ISS-001", run_id="dead-worker", now=time.time())
    report = await sched.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}  # recovered, then both built
    assert store.load_issue(slug, "ISS-001").status == IssueStatus.MERGED
    assert locks.active(integ) == {}  # dead worker's lock released


@pytest.mark.asyncio
async def test_live_foreign_lock_blocks_without_spinning(tmp_path):
    """WS4.2: a live foreign lock blocks an issue (no infinite re-dispatch)."""
    from foreman import locks
    import time
    repo, store, slug = await _prepare_feature(tmp_path)
    sched = _scheduler(store, _config())
    await sched.worktrees.ensure_base()
    integ = await sched.worktrees.integration_worktree()
    # A fresh foreign lock on ISS-001 (heartbeat = now) — looks alive.
    locks.acquire(integ, "ISS-001", run_id="other-proc", now=time.time())
    report = await sched.build(slug)  # must terminate, not hang
    assert "ISS-001" not in report.merged
    assert "ISS-001" in report.blocked or store.load_issue(slug, "ISS-001").status \
        == IssueStatus.QUEUED


@pytest.mark.asyncio
async def test_janitor_pass_runs_and_is_gated(tmp_path):
    """WS4.3: janitor passes run on cadence, as kind=janitor issues, gated like any work."""
    repo, store, slug = await _prepare_feature(tmp_path)
    cfg = _config()
    cfg.janitor_enabled = True
    cfg.janitor_every = 1            # a pass after every merged feature issue
    cfg.janitor_kinds = ["dedup", "docs"]
    sched = _scheduler(store, cfg)
    report = await sched.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}
    # Janitor issues were created (kind=janitor) and ran through the pipeline.
    state = store.load_feature(slug)
    jan = [i for i in state.issues if i.is_janitor]
    assert jan, "expected janitor issues to be created"
    assert all(i.status in (IssueStatus.MERGED, IssueStatus.DONE) for i in jan)
    assert report.janitor  # surfaced in the report
    assert {kind for _, kind, _ in report.janitor} <= {"dedup", "docs"}
    # Janitors were verified: their verification.json entries are set by Foreman.
    v = store.verification(slug)
    assert any(jid in v and v[jid].passes for jid in (i.id for i in jan))


@pytest.mark.asyncio
async def test_janitor_disabled_runs_no_pass(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    cfg = _config()
    cfg.janitor_enabled = False
    sched = _scheduler(store, cfg)
    report = await sched.build(slug)
    assert report.janitor == []
    assert not [i for i in store.load_feature(slug).issues if i.is_janitor]


@pytest.mark.asyncio
async def test_daily_cost_ceiling_stops_build(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    cfg = _config(daily=0.001)  # tiny ceiling
    # Pre-spend over the ceiling.
    CostLedger(store.paths.daily_cost_file).add(1.0)
    sched = _scheduler(store, cfg)
    report = await sched.build(slug)
    assert "ceiling" in report.stopped_reason
    # No issue should have completed.
    assert report.merged == []
