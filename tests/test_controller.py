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
