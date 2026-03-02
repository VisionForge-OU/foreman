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

    report = await c.build(slug)
    assert set(report.merged) == {"ISS-001", "ISS-002"}
    # Live worker buffers populated for the TUI.
    assert "ISS-001" in c.workers
    assert c.workers["ISS-001"].lines
    # Fail-first issue escalation rang the bell at least transiently is not
    # required (it retried), but cost was tracked.
    assert c.feature_cost(slug) > 0
