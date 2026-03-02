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
    from foreman.demo_scripts import _init, _result, _summary_block, WORKER_CODE, _write_worker_code
    from foreman.stream_parser import parse_event

    repo, store, slug = await _prepare_feature(tmp_path)
    # Replace the (dependent) sliced issues with two INDEPENDENT ones.
    for i in store.load_feature(slug).issues:
        store.delete_issue(slug, i.id)
    for iid in ("ISS-001", "ISS-002"):
        body = "## Goal\nx\n## Acceptance criteria (testable)\n- [ ] works\n"
        store.write_issue(slug, Issue(id=iid, title=iid, depends_on=[],
                                      branch=f"feature/{slug}/{iid.lower()}",
                                      budget=Budget(), prd_refs=["PRD §1"], body=body))

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
        yield parse_event({"type": "assistant", "message": {"content": [
            {"type": "text", "text": _summary_block(spec.label, written, True)}],
            "usage": {"input_tokens": 1}}})
        yield _result()

    scripts = demo_scripts()
    scripts["tdd"] = barrier_script
    sched = _scheduler(store, _config(max_parallel=2), scripts=scripts)
    report = await sched.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}


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
