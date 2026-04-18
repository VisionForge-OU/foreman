from pathlib import Path

import pytest
from textual.widgets import ListView, Static

from foreman.tui.app import (
    AttentionScreen, DashboardScreen, MetricsScreen, RetroScreen, ReviewScreen,
    SettingsScreen, WorkerScreen, ForemanTUI,
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
        oq_text = str(screen.query_one("#oq", Static).content)
        digest_text = str(screen.query_one("#digest", Static).content)
        assert "OPEN QUESTION" in oq_text
        assert "Should completing an already-completed item" in oq_text
        assert "Decisions made on your behalf" in digest_text
        assert "separate verb" in digest_text

        screen.action_approve()
        await pilot.pause()
        from foreman.models import DocStatus
        assert c.feature(slug).doc("prd").status != DocStatus.APPROVED

        screen.kind = "adr"
        screen.refresh_doc()
        await pilot.pause()
        adr_digest = str(screen.query_one("#digest", Static).content)
        assert "Decisions made on your behalf" in adr_digest
        assert "existing item record" in adr_digest


@pytest.mark.asyncio
async def test_review_screen_reject_amendment_creates_fix_issues(tmp_path):
    """H6: requesting changes on a PRD amendment via the ReviewScreen creates fix
    issues and reports that — not the misleading 're-run grill/planner' message."""
    import json

    from textual.widgets import TextArea

    from foreman.audit import AMENDMENT_HEADING
    from foreman.models import DocStatus

    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        c = app.controller
        slug = c.create_feature("Add done", "mark todos done")
        c.store.write_doc(slug, "adr", "# ADR")
        c.store.approve_doc(slug, "adr", "reviewer")
        c.store.write_doc(
            slug, "prd",
            "# PRD\n\n## User Flows\n- complete a todo\n\n" + AMENDMENT_HEADING
            + "\n\n1. **Re-complete is a no-op**\n   - Observed behaviour: raises\n",
            status=DocStatus.IN_REVIEW,
        )
        ap = c.store.paths.run_audit(slug, "s0001")
        ap.parent.mkdir(parents=True, exist_ok=True)
        ap.write_text(json.dumps({"schema": "foreman-audit/v1", "requirements": [
            {"requirement": "Re-complete is a no-op", "status": "diverged",
             "evidence": "todo/store.py", "note": "raises"}]}))
        app.current_slug = slug

        screen = ReviewScreen(slug)
        app.push_screen(screen)
        await pilot.pause()
        screen.kind = "prd"
        screen.refresh_doc()
        await pilot.pause()
        screen.query_one("#comments", TextArea).text = "spec is right; fix the code"
        msgs: list[str] = []
        screen.notify = lambda msg, **k: msgs.append(msg)
        screen.action_request_changes()
        await pilot.pause()

        assert any(i.id.startswith("FIX-") for i in c.feature(slug).issues)
        assert c.feature(slug).doc("prd").status == DocStatus.APPROVED
        assert any("fix issue" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_retro_screen_reviews_proposal_and_enforces_gate(tmp_path):
    """H7: the retro screen shows a proposal's diff + bench delta, lands only with
    approval AND a bench report, and blocks landing otherwise."""
    from foreman.retro import bench, driver, retro as retro_mod

    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        c = app.controller
        name = driver.draft(c.store, [retro_mod.PatchProposal(
            target="skill:foreman-tdd", title="Forbid mocking the unit",
            rationale="clustered bounces", diff="-mock\n+real", version_bump=1)])[0]

        screen = RetroScreen()
        app.push_screen(screen)
        await pilot.pause()
        body = str(screen.query_one("#pbody", Static).content)
        assert "Retro patch proposal" in body and "none attached" in body
        screen.selected = name

        msgs: list[str] = []
        screen.notify = lambda m, **k: msgs.append(m)
        # Land without approval/bench → gate error, nothing lands.
        screen.action_land()
        await pilot.pause()
        assert c.retro_proposal(name).status == "in_review"
        assert any("not approved" in m or "bench" in m for m in msgs), msgs

        # Approve + attach a bench report → lands.
        screen.action_approve()
        await pilot.pause()
        driver.attach_bench(c.store, name, bench.BenchReport(
            results=[bench.BenchResult("c", "success_first_try", 0.0, 1, True)],
            success_rate=1.0, total_cost=0.0, mean_turns=1.0))
        msgs.clear()
        screen.action_land()
        await pilot.pause()
        assert any("landed" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_dashboard_opens_retro_screen(tmp_path):
    """H7: the dashboard has a binding to open the retro proposal review screen."""
    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        app.screen.action_retro()
        await pilot.pause()
        assert isinstance(app.screen, RetroScreen)


@pytest.mark.asyncio
async def test_queue_review_shows_checks_touches_refs_graph_and_confirms(tmp_path):
    from foreman.models import Issue, Phase

    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test(size=(110, 34)) as pilot:
        c = app.controller
        slug = c.create_feature("Add tagging", "Notes can carry tags.")
        c.store.write_doc(slug, "adr", "# ADR")
        c.store.approve_doc(slug, "adr", "reviewer")
        c.store.write_doc(slug, "prd", "# PRD")
        c.store.approve_doc(slug, "prd", "reviewer")
        c.store.write_issue(slug, Issue(
            id="ISS-001",
            title="Model + POST tags",
            acceptance_check="tests/test_notes.py::test_create_with_tags",
            touches=["app/store.py", "tests/test_notes.py"],
            prd_refs=["PRD §Solution", "Story #1"],
        ))
        c.store.write_issue(slug, Issue(
            id="ISS-002",
            title="GET /tags summary",
            acceptance_check="tests/test_notes.py::test_get_tags",
            touches=["app/tags.py", "tests/test_tags.py"],
            prd_refs=["PRD §User Flows", "Story #3"],
        ))
        c.store.write_issue(slug, Issue(
            id="ISS-003",
            title="GET /notes?tag filter",
            depends_on=["ISS-001"],
            acceptance_check="tests/test_notes.py::test_filter_by_tag",
            touches=["app/store.py", "app/main.py", "tests/test_notes.py"],
            prd_refs=["PRD §User Flows", "Story #2"],
        ))
        app.current_slug = slug
        app.screen.refresh_data()
        await pilot.pause()

        assert c.feature(slug).phase == Phase.QUEUE_REVIEW
        body = str(app.screen.query_one("#board", Static).content)
        assert "Queue review" in body
        assert "acceptance_check: tests/test_notes.py::test_create_with_tags" in body
        assert "touches: app/store.py, tests/test_notes.py" in body
        assert "prd_refs: PRD §Solution, Story #1" in body
        assert "depends_on: ISS-001" in body
        assert "Conflict graph:" in body
        assert "ISS-001 ↔ ISS-003" in body
        assert "no overlaps: ISS-002" in body

        app.screen.action_confirm_queue()
        await pilot.pause()
        assert c.feature(slug).queue_confirmed is True


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


@pytest.mark.asyncio
async def test_attention_enter_is_newline_ctrl_s_submits(tmp_path, monkeypatch):
    """Enter in the answer box must insert a newline (not submit); Ctrl+S submits &
    resumes even while the TextArea is focused — the two were conflated on Enter."""
    from textual.widgets import TextArea
    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        c = app.controller
        slug = c.create_feature("x", "y")
        monkeypatch.setattr(c, "escalations", lambda s: [("ISS-001", "which store?")])
        captured = {}
        async def fake_resume(s, iid, answer):
            captured["args"] = (s, iid, answer)
        monkeypatch.setattr(c, "resume", fake_resume)

        screen = AttentionScreen(slug)
        app.push_screen(screen)
        await pilot.pause()
        assert screen.selected == "ISS-001"

        ta = screen.query_one("#answer", TextArea)
        ta.focus()
        await pilot.pause()
        await pilot.press("u", "s", "e", "enter", "i", "t")   # type, with a newline
        await pilot.pause()
        assert "\n" in ta.text                 # Enter inserted a newline…
        assert "args" not in captured          # …and did NOT submit

        expected = ta.text.strip()
        await pilot.press("ctrl+s")            # the dedicated submit, from inside the box
        await pilot.pause()
        assert captured.get("args") == (slug, "ISS-001", expected)


@pytest.mark.asyncio
async def test_attention_resume_consumes_answer_into_new_session(tmp_path):
    """H5 end-to-end (no monkeypatch): an issue escalates, the human answers in the
    AttentionScreen, and the REAL resume feeds that answer into the resumed worker's
    prompt (a new session) — then the issue proceeds and merges."""
    from textual.widgets import TextArea

    from foreman.demo_scripts import demo_scripts, make_tdd_script
    from foreman.models import IssueStatus

    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        c = app.controller
        c.config.limits.max_retries = 1
        # ISS-001 always fails its gate → escalates to the attention queue.
        c.backend.scripts["tdd:ISS-001"] = lambda spec: make_tdd_script(fail_first=True)(spec)
        slug = c.create_feature("Add done", "mark todos done")
        await c.run_planner(slug); c.approve(slug, "plan")
        await c.run_grill(slug); c.request_changes(slug, "prd", "no-op please")
        await c.run_grill(slug); c.approve(slug, "adr"); c.approve(slug, "prd")
        await c.run_slicer(slug); c.confirm_queue(slug)
        app.current_slug = slug
        await c.build(slug)
        assert c.feature(slug).issue("ISS-001").status == IssueStatus.NEEDS_HUMAN

        # Capture the resumed worker's prompt; give it a passing script this time.
        prompts: list[str] = []

        def capture(spec):
            prompts.append(spec.prompt)
            return demo_scripts()["tdd"](spec)

        c.backend.scripts["tdd:ISS-001"] = capture

        screen = AttentionScreen(slug)
        app.push_screen(screen)
        await pilot.pause()
        assert screen.selected == "ISS-001"
        screen.query_one("#answer", TextArea).text = "Use the in-memory store; dedupe tags silently."
        screen.action_resume()
        for _ in range(120):
            await pilot.pause(0.05)
            if c.feature(slug).issue("ISS-001").status == IssueStatus.MERGED:
                break

        assert c.feature(slug).issue("ISS-001").status == IssueStatus.MERGED
        # The answer reached the NEW session's prompt — genuinely consumed, not just filed.
        assert any("Use the in-memory store" in p for p in prompts), prompts
        assert "Reviewer answer" in c.store.paths.escalation_file(slug, "ISS-001").read_text()


@pytest.mark.asyncio
async def test_attention_refresh_escs_safe_after_screen_unmount(tmp_path):
    """The resume worker calls refresh_escs after its await; if the user navigated away
    and the screen was torn down, it must be a safe no-op, not a NoMatches crash."""
    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        slug = app.controller.create_feature("x", "y")
        screen = AttentionScreen(slug)
        app.push_screen(screen)
        await pilot.pause()
        assert screen.query("#elist")            # mounted
        app.pop_screen()
        await pilot.pause()
        assert not screen.query("#elist")        # widgets gone (is_mounted is unreliable here)
        screen.refresh_escs()                    # previously raised NoMatches on '#elist'


@pytest.mark.asyncio
async def test_attention_resume_outliving_screen_does_not_crash(tmp_path, monkeypatch):
    """End-to-end: a long resume that finishes after the user leaves the screen must
    not crash the app touching the unmounted screen's widgets."""
    import asyncio
    from textual.widgets import TextArea
    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        c = app.controller
        slug = c.create_feature("x", "y")
        monkeypatch.setattr(c, "escalations", lambda s: [("ISS-001", "which store?")])
        gate = asyncio.Event()
        called = {}
        async def slow_resume(s, iid, a):
            called["args"] = (s, iid, a)
            await gate.wait()
        monkeypatch.setattr(c, "resume", slow_resume)

        screen = AttentionScreen(slug)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#answer", TextArea).text = "retry it"
        screen.action_resume()                   # spawn resume worker (awaiting gate)
        await pilot.pause()
        assert called["args"] == (slug, "ISS-001", "retry it")
        app.pop_screen()                         # user navigates away mid-resume
        await pilot.pause()
        gate.set()                               # resume completes → worker calls refresh_escs
        await pilot.pause()
        await pilot.pause()
        assert app.is_running                    # no crash


@pytest.mark.asyncio
async def test_worker_log_with_unbalanced_brackets_does_not_crash(tmp_path):
    """Worker log lines are raw agent output (shell commands) and routinely contain an
    unbalanced '[' (a truncated `if [ -f ...`). Rendering them must not be parsed as
    Textual markup → MarkupError."""
    from foreman.tui.controller import WorkerLog
    app = ForemanTUI(repo_root=tmp_path, demo=True)
    async with app.run_test() as pilot:
        w = WorkerLog("ISS-002", status="running")
        w.lines = [
            "  ⚙ Bash(command=cd /repo && if [ -f .foreman/init.sh ]; then bash .forem",
            "  Let me run the init script:",
            "  ⚙ Bash(command=cd /repo && bash .foreman/)",
        ]
        app.controller.workers["ISS-002"] = w
        # also exercise the dashboard global log + status bar with the same text
        app.controller.global_log.append(w.lines[0])
        app.push_screen(WorkerScreen())
        await pilot.pause()
        app.screen.selected = "ISS-002"
        app.screen.refresh_workers()        # previously raised MarkupError
        await pilot.pause()
        assert app.is_running


@pytest.mark.asyncio
async def test_tui_loads_notesapi_dogfood_state():
    """Dogfood e2e: mount the TUI against the persisted notesapi Foreman state."""
    repo = Path("/home/arash/foreman-validation/notesapi")
    if not (repo / ".foreman" / "features").exists():
        pytest.skip("notesapi validation repo is not available")

    app = ForemanTUI(repo_root=repo)
    async with app.run_test(size=(100, 32)) as pilot:
        assert isinstance(app.screen, DashboardScreen)
        await pilot.pause(0.5)

        features = app.controller.features()
        assert "add-date-to-the-notes" in features
        app.current_slug = "add-date-to-the-notes"
        app.screen._build_feature_list()
        app.screen.refresh_data()
        app.screen.refresh_status()
        await pilot.pause(0.2)

        board = app.screen.query_one("#board", Static)
        board_text = str(board.content)
        assert "ISS-001" in board_text
        assert "ISS-002" in board_text
        assert "ISS-…" not in board_text

        assert app.controller.escalations(app.current_slug)

        app.push_screen(AttentionScreen(app.current_slug))
        await pilot.pause(0.2)
        assert isinstance(app.screen, AttentionScreen)
        app.pop_screen()
        await pilot.pause(0.2)

        app.push_screen(ReviewScreen(app.current_slug))
        await pilot.pause(0.2)
        assert isinstance(app.screen, ReviewScreen)
        app.pop_screen()
        await pilot.pause(0.2)

        app.push_screen(MetricsScreen(app.current_slug))
        await pilot.pause(0.2)
        assert isinstance(app.screen, MetricsScreen)
        app.pop_screen()
        await pilot.pause(0.2)

        app.push_screen(SettingsScreen())
        await pilot.pause(0.2)
        assert isinstance(app.screen, SettingsScreen)
        app.pop_screen()
        await pilot.pause(0.2)

        assert app.is_running
