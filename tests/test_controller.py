import pytest

from foreman.models import DocStatus, IssueStatus
from foreman.tui.controller import Controller


@pytest.mark.asyncio
async def test_controller_demo_drives_pipeline(tmp_path):
    c = Controller(tmp_path, demo=True)
    assert c.missing_required() == []
    assert c.features() == []

    slug = c.create_feature("Add done", "mark todos done")
    plan = await c.run_planner(slug)
    assert plan.status == DocStatus.IN_REVIEW

    c.approve(slug, "plan")
    await c.run_grill(slug)
    c.request_changes(slug, "prd", "no-op please")
    await c.run_grill(slug)
    c.approve(slug, "adr")
    c.approve(slug, "prd")
    await c.run_slicer(slug)
    c.confirm_queue(slug)

    # Phase-A agents now feed the live status line + global log (previously silent).
    assert any("planner" in line for line in c.global_log)
    assert c.activity is None            # cleared after each phase completes
    assert c.status_line() == "idle"

    report = await c.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}
    # Live worker buffers populated for the TUI.
    assert "ISS-001" in c.workers
    assert c.workers["ISS-001"].lines
    # Fail-first issue escalation rang the bell at least transiently is not
    # required (it retried), but cost was tracked.
    assert c.feature_cost(slug) > 0


@pytest.mark.asyncio
async def test_worker_finished_surfaces_killed_turns(tmp_path):
    """Issue #1: a turn-killed run is loud in the worker log (no longer silent)."""
    from foreman.models import RunRecord
    from foreman.runner import RunResult

    c = Controller(tmp_path, demo=True)
    rec = RunRecord(
        run_id="r1-ISS-001", label="ISS-001",
        started="2026-06-20T10:00:00Z", finished="2026-06-20T10:05:00Z",
        num_turns=30, cost_usd=0.25, terminal_reason="killed_turns",
    )
    c.worker_finished("ISS-001", "tests_failing", RunResult(rec, "", None, None))
    line = c.workers["ISS-001"].lines[-1]
    assert "killed_turns" in line
    assert "⚠" in line


@pytest.mark.asyncio
async def test_worker_finished_clean_run_has_no_warning(tmp_path):
    from foreman.models import RunRecord
    from foreman.runner import RunResult

    c = Controller(tmp_path, demo=True)
    rec = RunRecord(
        run_id="r1-ISS-001", label="ISS-001",
        started="2026-06-20T10:00:00Z", finished="2026-06-20T10:05:00Z",
        num_turns=12, cost_usd=0.10, terminal_reason="completed",
    )
    c.worker_finished("ISS-001", "done", RunResult(rec, "", None, None))
    line = c.workers["ISS-001"].lines[-1]
    assert "⚠" not in line
    assert "completed" not in line   # a clean finish stays terse


@pytest.mark.asyncio
async def test_request_changes_on_prd_amendment_creates_fix_issues(tmp_path):
    """H6: requesting changes on a PRD that carries an auto-drafted amendment
    rejects the amendment and spins off fix issues (returns their ids)."""
    import json

    from foreman.audit import AMENDMENT_HEADING

    c = Controller(tmp_path, demo=True)
    slug = c.create_feature("Add done", "mark todos done")
    c.store.write_doc(slug, "adr", "# ADR")
    c.store.approve_doc(slug, "adr", "reviewer")
    c.store.write_doc(
        slug, "prd",
        "# PRD\n\n## User Flows\n- complete a todo\n\n" + AMENDMENT_HEADING
        + "\n\n1. **Re-complete is a no-op**\n   - Observed behaviour: raises\n",
        status=DocStatus.IN_REVIEW,
    )
    audit_path = c.store.paths.run_audit(slug, "s0001")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps({
        "schema": "foreman-audit/v1",
        "requirements": [{"requirement": "Re-complete is a no-op", "status": "diverged",
                          "evidence": "todo/store.py", "note": "raises on re-complete"}],
    }))

    created = c.request_changes(slug, "prd", "the spec is right; fix the code")
    assert created, "amendment rejection must return the new fix-issue ids"
    state = c.feature(slug)
    assert [i for i in state.issues if i.id in created]
    # The approved spec stands again; the amendment is gone.
    assert state.doc("prd").status == DocStatus.APPROVED
    assert AMENDMENT_HEADING not in state.doc("prd").body


@pytest.mark.asyncio
async def test_request_changes_on_ordinary_prd_does_not_create_issues(tmp_path):
    """A normal request-changes (no amendment) stays the plain revise loop."""
    c = Controller(tmp_path, demo=True)
    slug = c.create_feature("Add done", "mark todos done")
    c.store.write_doc(slug, "prd", "# PRD\n\nplain body\n", status=DocStatus.IN_REVIEW)
    result = c.request_changes(slug, "prd", "please split the model change")
    assert not result
    assert c.feature(slug).doc("prd").status == DocStatus.CHANGES_REQUESTED
    assert not c.feature(slug).issues


@pytest.mark.asyncio
async def test_controller_proposal_review_and_landing_gate(tmp_path):
    """H7: the controller surfaces retro proposals and enforces the landing gate
    (approval AND an attached bench report) for the TUI."""
    from foreman.retro import bench, driver, retro as retro_mod

    c = Controller(tmp_path, demo=True)
    p = retro_mod.PatchProposal(target="skill:foreman-tdd", title="Forbid mocking the unit",
                                rationale="clustered bounces", diff="add a line", version_bump=1)
    name = driver.draft(c.store, [p])[0]

    props = c.retro_proposals()
    assert any(sp.name == name and sp.status == "in_review" for sp in props)
    assert "Retro patch proposal" in c.proposal_detail(name)

    with pytest.raises(ValueError):       # cannot land before approval
        c.land_proposal(name)
    c.approve_proposal(name)
    assert c.retro_proposal(name).sealed
    with pytest.raises(ValueError, match="bench"):  # cannot land without bench
        c.land_proposal(name)

    report = bench.BenchReport(results=[bench.BenchResult("c", "success_first_try", 0.0, 1, True)],
                               success_rate=1.0, total_cost=0.0, mean_turns=1.0)
    driver.attach_bench(c.store, name, report)
    assert c.land_proposal(name).startswith("landed")


@pytest.mark.asyncio
async def test_controller_reject_proposal_blocks_landing(tmp_path):
    """H7: a rejected proposal cannot land."""
    from foreman.retro import driver, retro as retro_mod

    c = Controller(tmp_path, demo=True)
    name = driver.draft(c.store, [retro_mod.PatchProposal(
        target="skill:foreman-tdd", title="t", rationale="r", diff="d")])[0]
    c.reject_proposal(name)
    assert c.retro_proposal(name).status == "rejected"
    with pytest.raises(ValueError):
        c.land_proposal(name)


def test_status_line_tracks_phase_activity(tmp_path):
    """The status line reflects a running Phase-A agent: label, turns, last line."""
    from foreman.stream_parser import parse_event

    c = Controller(tmp_path, demo=True)
    assert c.status_line() == "idle"

    c.begin_activity("planner", "planner")
    assert c.activity is not None and c.activity.running
    ev = parse_event({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "id": "t1", "input": {"command": "pytest -q"}}],
        "usage": {"input_tokens": 1, "output_tokens": 1}}})
    c._on_phase_event(ev)
    assert c.activity.turns == 1
    sl = c.status_line()
    assert "planner" in sl and "turn 1" in sl
    assert any("Bash" in line for line in c.global_log)   # fed the live log too

    c.end_activity(True)
    assert c.activity is None
    assert c.status_line() == "idle"


def test_status_line_build_summarizes_running_workers(tmp_path):
    from foreman.tui.controller import WorkerLog

    c = Controller(tmp_path, demo=True)
    c.begin_activity("build", "build")
    w = c.workers.setdefault("ISS-001", WorkerLog("ISS-001"))
    w.status, w.turns, w.cost = "running", 3, 0.02
    sl = c.status_line()
    assert "build" in sl and "ISS-001" in sl and "worker" in sl and "3t" in sl
