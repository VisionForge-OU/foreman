import pytest

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
