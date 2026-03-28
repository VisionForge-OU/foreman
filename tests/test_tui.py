import pytest
from textual.widgets import ListView

from foreman.tui.app import (
    AttentionScreen, DashboardScreen, ReviewScreen, SettingsScreen,
    WorkerScreen, ForemanTUI,
)


@pytest.mark.asyncio
async def test_tui_mounts_and_navigates(tmp_path):
    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        # Dashboard is up.
        assert isinstance(app.screen, DashboardScreen)
        # Skill panel present and demo init installed all required skills.
        app.screen.query_one("#skills")
        assert app.controller.missing_required() == []

        # Create a feature directly via the controller, refresh, select it.
        slug = app.controller.create_feature("Add done", "mark todos done")
        app.current_slug = slug
        app.screen._build_feature_list()
        app.screen.refresh_data()
        await pilot.pause()

        # Selecting the feature in the list must not crash and must set the slug
        # from the ListItem name (regression: Label.renderable removed in Textual 8).
        app.current_slug = None
        flist = app.screen.query_one("#flist", ListView)
        flist.focus()
        flist.index = 0
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.current_slug == slug

        # The persistent status bar renders without error and reports idle initially.
        app.screen.query_one("#statusbar")        # widget exists
        app.screen.refresh_status()               # updates without raising
        assert app.controller.status_line() == "idle"

        # Run the planner via the dashboard action; wait for the worker.
        app.screen.action_planner()
        for _ in range(50):
            await pilot.pause(0.05)
            if app.controller.feature(slug).doc("plan") is not None:
                break
        assert app.controller.feature(slug).doc("plan") is not None

        # Push each screen; ensure they mount without error.
        app.push_screen(ReviewScreen(slug))
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        app.pop_screen()
        await pilot.pause()

        app.push_screen(WorkerScreen())
        await pilot.pause()
        app.pop_screen()
        await pilot.pause()

        app.push_screen(AttentionScreen(slug))
        await pilot.pause()
        app.pop_screen()
        await pilot.pause()

        app.push_screen(SettingsScreen())
        await pilot.pause()
        app.screen.query_one("#cfg")  # settings rendered without error
        assert app.controller.config.permission_mode == "acceptEdits"
        app.pop_screen()
        await pilot.pause()


@pytest.mark.asyncio
async def test_review_screen_blocks_approval_with_open_questions(tmp_path):
    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        c = app.controller
        slug = c.create_feature("x", "y")
        await c.run_planner(slug)
        c.approve(slug, "plan")
        await c.run_grill(slug)  # PRD v1 has an open question
        app.current_slug = slug

        screen = ReviewScreen(slug)
        app.push_screen(screen)
        await pilot.pause()
        screen.kind = "prd"
        screen.refresh_doc()
        await pilot.pause()
        # Approving a doc with open questions must fail and keep it un-approved.
        screen.action_approve()
        await pilot.pause()
        from foreman.models import DocStatus
        assert c.feature(slug).doc("prd").status != DocStatus.APPROVED


@pytest.mark.asyncio
async def test_worker_screen_list_stable_and_selectable(tmp_path):
    """Regression: the worker sidebar must NOT clear+rebuild every refresh (that
    flickered, broke arrow nav, and crashed clicks). Labels update in place; the list
    rebuilds only when the worker set changes; selecting an item updates state."""
    from foreman.tui.controller import WorkerLog

    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        c = app.controller
        c.workers["init"] = WorkerLog("init", status="done")
        c.workers["ISS-001"] = WorkerLog("ISS-001")
        c.workers["ISS-001"].lines = ["hello from the worker"]

        app.push_screen(WorkerScreen())
        await pilot.pause()
        screen = app.screen
        lv = screen.query_one("#wlist", ListView)
        assert [i.name for i in lv.children] == ["init", "ISS-001"]

        # Steady-state refresh (only a label changed) must reuse the SAME ListItems.
        before = [id(i) for i in lv.children]
        c.workers["ISS-001"].turns = 5
        screen.refresh_workers()
        await pilot.pause()
        assert [id(i) for i in lv.children] == before     # no clear/rebuild → no flicker

        # A membership change DOES rebuild.
        c.workers["ISS-002"] = WorkerLog("ISS-002")
        screen.refresh_workers()
        await pilot.pause()
        assert [i.name for i in lv.children] == ["init", "ISS-001", "ISS-002"]

        # Moving the highlight (arrow/tab/click) updates the selection without crashing.
        lv.index = 1
        await pilot.pause()
        assert screen.selected == "ISS-001"
