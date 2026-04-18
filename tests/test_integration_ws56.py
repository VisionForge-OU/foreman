"""WS5/WS6 integration — auditor amendment, outcome labels, notify, retro gate, CLI."""

import itertools
import json

import pytest

from foreman.backend import MockBackend
from foreman.config import Config
from foreman.demo_scripts import demo_scripts, make_auditor_script
from foreman.installer import init_repo
from foreman.ledger import CostLedger
from foreman.models import DocStatus, IssueStatus, Phase
from foreman.sample import create_sample_repo, pytest_command
from foreman.scheduler import Scheduler
from foreman.state import FileStore


def _config():
    cfg = Config()
    cfg.commands = {"test": pytest_command(), "lint": "", "typecheck": "", "e2e": ""}
    cfg.e2e_enabled = False
    cfg.stuck_turns = 0
    return cfg


async def _prepare(tmp_path, scripts=None):
    repo = create_sample_repo(tmp_path / "repo")
    init_repo(repo)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(itertools.count(1)):02d}Z")
    from foreman.pipeline import Pipeline
    rc = itertools.count(1)
    pipe = Pipeline(store, _config(), MockBackend(demo_scripts()),
                    run_id_clock=lambda: f"p{next(rc):04d}")
    slug = store.create_feature("todo done", "Add a done command")
    await pipe.run_planner(slug); store.approve_doc(slug, "plan", "a")
    await pipe.run_grill(slug); store.request_changes(slug, "prd", "a", "no-op")
    await pipe.run_grill(slug); store.approve_doc(slug, "prd", "a"); store.approve_doc(slug, "adr", "a")
    await pipe.run_slicer(slug); store.confirm_queue(slug)
    return repo, store, slug


def _sched(store, cfg, scripts=None):
    rc = itertools.count(1)
    return Scheduler(store, cfg, MockBackend(scripts or demo_scripts()),
                     ledger=CostLedger(store.paths.daily_cost_file),
                     run_id_clock=lambda: f"s{next(rc):04d}")


# --- WS5.1 auditor → PRD amendment through the hash-sealed gate --- #

@pytest.mark.asyncio
async def test_auditor_divergence_drafts_prd_amendment(tmp_path):
    repo, store, slug = await _prepare(tmp_path)
    scripts = demo_scripts()
    scripts["auditor"] = make_auditor_script(requirements=[
        {"requirement": "Re-completing is a no-op", "status": "diverged",
         "evidence": "todo/store.py", "note": "actually raises on re-complete"},
    ])
    cfg = _config()
    cfg.e2e_enabled = True  # auditor runs after the (no-op) e2e gate
    sched = _sched(store, cfg, scripts=scripts)
    report = await sched.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}
    # PRD approval auto-invalidated → back in review with an amendment section.
    prd = store.load_feature(slug).doc("prd")
    assert prd.status == DocStatus.IN_REVIEW
    assert "PRD Amendment" in prd.body
    assert "amendment_drafted" in (report.audit or "")
    # The audit was persisted.
    assert any(p.name == "audit.json" for p in store.paths.runs_dir(slug).glob("*/audit.json"))


@pytest.mark.asyncio
async def test_reject_amendment_spins_off_fix_issues(tmp_path):
    """H6: rejecting an auto-drafted PRD amendment turns the divergence into a
    concrete, buildable fix issue (not a silent drop) and keeps the approved spec."""
    repo, store, slug = await _prepare(tmp_path)
    scripts = demo_scripts()
    scripts["auditor"] = make_auditor_script(requirements=[
        {"requirement": "Re-completing is a no-op", "status": "diverged",
         "evidence": "todo/store.py", "note": "actually raises on re-complete"},
    ])
    cfg = _config(); cfg.e2e_enabled = True
    sched = _sched(store, cfg, scripts=scripts)
    await sched.build(slug)
    prd = store.load_feature(slug).doc("prd")
    assert prd.status == DocStatus.IN_REVIEW and "PRD Amendment" in prd.body

    created = sched.reject_amendment(slug, "the spec is right; fix the code")
    assert created, "rejecting an amendment must create fix issue(s)"

    state = store.load_feature(slug)
    fix = [i for i in state.issues if i.id in created]
    assert fix and all(i.status == IssueStatus.QUEUED for i in fix)
    # Each fix issue must be buildable (WS1.1: a runnable acceptance check).
    assert all(i.acceptance_check.strip() for i in fix)
    # The approved spec stands: the amendment is dropped and the PRD re-sealed.
    prd2 = state.doc("prd")
    assert prd2.status == DocStatus.APPROVED and "PRD Amendment" not in prd2.body
    # The feature drops back into BUILDING so the fix issues can run.
    assert state.phase == Phase.BUILDING


@pytest.mark.asyncio
async def test_rejected_amendment_fix_issue_builds_and_merges(tmp_path):
    """H6 end-to-end: after rejecting an amendment, the spun-off fix issue builds
    and merges on the next build pass."""
    repo, store, slug = await _prepare(tmp_path)
    scripts = demo_scripts()
    scripts["auditor"] = make_auditor_script(requirements=[
        {"requirement": "Re-completing is a no-op", "status": "diverged",
         "evidence": "todo/store.py", "note": "raises on re-complete"},
    ])
    cfg = _config(); cfg.e2e_enabled = False
    sched = _sched(store, cfg, scripts=scripts)
    await sched.build(slug)
    created = sched.reject_amendment(slug)
    assert created

    # Re-build (default auditor = all satisfied) → the fix issue lands.
    sched2 = _sched(store, _config())
    report = await sched2.build(slug)
    assert set(created) <= set(report.merged)
    assert store.load_feature(slug).issue(created[0]).status == IssueStatus.MERGED


@pytest.mark.asyncio
async def test_auditor_satisfied_leaves_prd_approved(tmp_path):
    repo, store, slug = await _prepare(tmp_path)
    cfg = _config(); cfg.e2e_enabled = True
    sched = _sched(store, cfg)  # default auditor = all satisfied
    report = await sched.build(slug)
    assert store.load_feature(slug).doc("prd").status == DocStatus.APPROVED
    assert "satisfied" in (report.audit or "")


# --- WS6.1 outcome labels feed the metrics aggregation --- #

@pytest.mark.asyncio
async def test_outcome_labels_and_metrics(tmp_path):
    from foreman.retro import metrics
    repo, store, slug = await _prepare(tmp_path)
    sched = _sched(store, _config())
    await sched.build(slug)
    # Terminal run records carry success labels.
    outcomes = [json.loads(u.read_text()).get("outcome", "")
                for u in store.paths.runs_dir(slug).glob("*-ISS-*/usage.json")]
    assert any(o == "success_first_try" for o in outcomes)
    m = metrics.load_feature_metrics(store, slug)
    assert m.success_rate == 1.0
    assert m.by_outcome.get("success_first_try", 0) >= 2


# --- WS5.3 notify_command fires on escalation --- #

@pytest.mark.asyncio
async def test_notify_fires_on_escalation(tmp_path, monkeypatch):
    repo, store, slug = await _prepare(tmp_path)
    calls = []
    monkeypatch.setattr("foreman.notify.fire",
                        lambda cmd, **kw: calls.append((cmd, kw)) or True)
    cfg = _config(); cfg.limits.max_retries = 1; cfg.notify_command = "true"
    from foreman.demo_scripts import make_tdd_script
    scripts = demo_scripts()
    scripts["tdd:ISS-001"] = lambda spec: make_tdd_script(fail_first=True)(spec)
    sched = _sched(store, cfg, scripts=scripts)
    await sched.build(slug)
    assert any(kw.get("event") == "escalation" for _, kw in calls)


# --- WS6.2/6.3 retro draft → gate → bench → land --- #

@pytest.mark.asyncio
async def test_retro_draft_gate_bench_land(tmp_path):
    from foreman.retro import driver, bench, retro as retro_mod
    repo, store, slug = await _prepare(tmp_path)
    await _sched(store, _config()).build(slug)  # produce run history

    # A retro agent that proposes one skill patch.
    proposal_json = ('```json\n{"schema":"foreman-retro/v1","proposals":[{'
                     '"target":"skill:foreman-tdd","title":"Forbid mocking the unit",'
                     '"rationale":"clustered test-honesty bounces","diff":"add a line",'
                     '"version_bump":1}]}\n```')
    from foreman.demo_scripts import _init, _result
    from foreman.stream_parser import parse_event

    async def retro_script(spec):
        yield _init(spec)
        yield parse_event({"type": "assistant", "message": {"content": [
            {"type": "text", "text": proposal_json}], "usage": {"input_tokens": 1}}})
        yield _result()

    backend = MockBackend({"retro": retro_script})
    proposals, clusters, _ = await driver.analyze(store, _config(), backend, slugs=[slug])
    assert len(proposals) == 1
    names = driver.draft(store, proposals)
    name = names[0]
    sp = driver.load(store, name)
    assert sp.status == "in_review" and not sp.sealed

    # Cannot land before approval.
    with pytest.raises(ValueError):
        driver.land(store, name)

    driver.approve(store, name)
    assert driver.load(store, name).sealed

    # Cannot land without a bench report (the hard WS6 rule).
    with pytest.raises(ValueError, match="bench"):
        driver.land(store, name)

    # Attach a bench report → now it lands and bumps the skill changelog.
    report = bench.BenchReport(results=[bench.BenchResult("c", "success_first_try", 0.0, 1, True)],
                               success_rate=1.0, total_cost=0.0, mean_turns=1.0)
    driver.attach_bench(store, name, report)
    msg = driver.land(store, name)
    assert "landed" in msg
    assert store.paths.skill_changelog_file.exists()


def test_cli_has_retro_and_bench():
    from foreman.cli import build_parser
    p = build_parser()
    sub = {a.dest: a for a in p._subparsers._group_actions} if p._subparsers else {}
    # The subcommands are registered.
    help_text = p.format_help()
    assert "retro" in help_text and "bench" in help_text
