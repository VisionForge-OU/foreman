"""Foreman TUI (Textual) — §8.

Screens: Dashboard, Review, Worker view, Attention queue, Settings. The app holds
a :class:`Controller`; agent work runs as Textual workers and the views refresh on
a short interval so 4+ workers can stream concurrently without flicker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, Footer, Header, Input, Label, ListItem, ListView, Markdown, Static, TextArea,
)

from ..models import DOC_KINDS, DocStatus, IssueStatus, Phase
from .controller import Controller

PHASE_HINT = {
    Phase.REQUEST: "Press [b]p[/b] to run the planner",
    Phase.PLAN_REVIEW: "Press [b]v[/b] to review the plan (a=approve, r=request changes)",
    Phase.GRILLING: "Press [b]g[/b] to run the grill (ADR + PRD)",
    Phase.DOC_REVIEW: "Press [b]v[/b] to review ADR/PRD",
    Phase.SLICING: "Press [b]s[/b] to run the slicer",
    Phase.QUEUE_REVIEW: "Press [b]c[/b] to confirm the queue, then [b]b[/b] to build",
    Phase.BUILDING: "Press [b]b[/b] to (re)start the build · [b]w[/b] workers · [b]x[/b] attention",
    Phase.DONE: "Feature complete 🎉  (see report.md)",
}


class NewFeatureScreen(ModalScreen[Optional[tuple]]):
    CSS = """
    NewFeatureScreen { align: center middle; }
    #box { width: 80; height: auto; border: round $accent; padding: 1 2; background: $panel; }
    #req { height: 10; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label("New feature — title")
            yield Input(placeholder="e.g. Add dark mode", id="title")
            yield Label("Request (description + product requirements)")
            yield TextArea(id="req")
            with Horizontal():
                yield Button("Create", variant="primary", id="create")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            title = self.query_one("#title", Input).value.strip()
            req = self.query_one("#req", TextArea).text.strip()
            if title:
                self.dismiss((title, req))
                return
        self.dismiss(None)


class ReviewScreen(Screen):
    BINDINGS = [
        Binding("a", "approve", "Approve"),
        Binding("r", "request_changes", "Request changes"),
        Binding("tab", "cycle_doc", "Next doc"),
        Binding("escape", "app.pop_screen", "Back"),
    ]
    CSS = """
    #oq { color: $warning; padding: 1 1; }
    #comments { height: 8; border: round $accent; }
    #doc { height: 1fr; }
    """

    def __init__(self, slug: str):
        super().__init__()
        self.slug = slug
        self.kind = "plan"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="title")
        yield Static(id="digest")   # WS5.2: "decisions made on your behalf"
        yield Static(id="oq")       # open questions first
        with VerticalScroll(id="doc"):
            yield Markdown(id="md")
        yield Label("Comments (used as answers to open questions):")
        yield TextArea(id="comments")
        yield Footer()

    def on_mount(self) -> None:
        # Default to the first doc that is awaiting review.
        st = self.app.controller.feature(self.slug)
        for kind in DOC_KINDS:
            d = st.doc(kind)
            if d and d.status in (DocStatus.IN_REVIEW, DocStatus.CHANGES_REQUESTED):
                self.kind = kind
                break
        self.refresh_doc()

    def refresh_doc(self) -> None:
        st = self.app.controller.feature(self.slug)
        d = st.doc(self.kind)
        title = self.query_one("#title", Static)
        md = self.query_one("#md", Markdown)
        oq = self.query_one("#oq", Static)
        if d is None:
            title.update(f"[b]{self.kind}[/b] — (not produced yet)")
            md.update("")
            oq.update("")
            return
        badges = self.app.controller.review_badges(self.slug, self.kind)
        title.update(f"[b]{self.kind.upper()}[/b]  v{d.version}  ·  status: {d.status.value}"
                     + (f"   ·   {badges}" if badges else ""))
        # WS5.2: surface the grill's "decisions made on your behalf" digest up top.
        from .. import review
        digest = review.decisions_digest(d.body)
        dwidget = self.query_one("#digest", Static)
        if digest:
            dwidget.update("[b]Decisions made on your behalf:[/b]\n"
                           + "\n".join(f"  • {x}" for x in digest))
        else:
            dwidget.update("")
        md.update(d.body)
        if d.has_open_questions:
            qs = "\n".join(f"  • {q}" for q in d.open_questions)
            oq.update(f"⚠ {len(d.open_questions)} OPEN QUESTION(S) — cannot approve until resolved:\n{qs}")
        else:
            oq.update("✓ No open questions — approvable.")

    def action_cycle_doc(self) -> None:
        idx = DOC_KINDS.index(self.kind)
        self.kind = DOC_KINDS[(idx + 1) % len(DOC_KINDS)]
        self.refresh_doc()

    def action_approve(self) -> None:
        try:
            self.app.controller.approve(self.slug, self.kind)
            self.notify(f"{self.kind} approved")
        except Exception as e:
            self.notify(str(e), severity="error")
        self.refresh_doc()

    def action_request_changes(self) -> None:
        comments = self.query_one("#comments", TextArea).text.strip()
        if not comments:
            self.notify("Add a comment first", severity="warning")
            return
        self.app.controller.request_changes(self.slug, self.kind, comments)
        self.query_one("#comments", TextArea).text = ""
        self.notify(f"changes requested on {self.kind}; re-run grill/planner to revise")
        self.refresh_doc()


class WorkerScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("k", "kill", "Kill worker"),
        Binding("tab", "next_worker", "Next worker"),
    ]
    CSS = """
    #wlist { width: 30; border-right: solid $accent; }
    #wlog { height: 1fr; }
    """

    def __init__(self):
        super().__init__()
        self.selected: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListView(id="wlist")
            with VerticalScroll(id="wlog"):
                yield Static(id="logbody")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.3, self.refresh_workers)
        self.refresh_workers()

    @staticmethod
    def _worker_label(iid: str, w) -> str:
        return f"{iid} [{w.status}] ${w.cost:.3f} {w.turns}t"

    def refresh_workers(self) -> None:
        workers = self.app.controller.workers
        lv = self.query_one("#wlist", ListView)
        ids = list(workers.keys())
        if self.selected is None and ids:
            self.selected = ids[0]
        # IMPORTANT: only rebuild the list when the SET of workers changes. Rebuilding
        # (clear + re-append) every tick made the sidebar flicker, wiped the arrow-key
        # highlight, and raced with click handling (Textual looked the clicked item up
        # in a node list we had just cleared → ValueError crash). In steady state we
        # update the existing labels in place instead.
        existing = [item.name for item in lv.children]
        if existing != ids:
            lv.clear()
            for iid in ids:
                lv.append(ListItem(Label(self._worker_label(iid, workers[iid])), name=iid))
            if self.selected in ids:
                lv.index = ids.index(self.selected)
        else:
            for item in lv.children:
                w = workers.get(item.name)
                if w is not None:
                    item.query_one(Label).update(self._worker_label(item.name, w))
        self._update_log()

    def _update_log(self) -> None:
        workers = self.app.controller.workers
        body = self.query_one("#logbody", Static)
        if self.selected and self.selected in workers:
            w = workers[self.selected]
            body.update(self._budget_bar(self.selected, w) + "\n" + "\n".join(w.lines[-300:]))
        else:
            body.update("No active workers. Start a build from the dashboard ([b]b[/b]).")

    def _budget_bar(self, iid: str, w) -> str:
        return f"[b]{iid}[/b]  status={w.status}  cost=${w.cost:.4f}  turns={w.turns}\n" + ("─" * 40)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        self.selected = event.item.name
        self._update_log()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Arrow-key navigation moves the highlight — follow it in the log pane.
        if event.item is not None and event.item.name:
            self.selected = event.item.name
            self._update_log()

    def action_next_worker(self) -> None:
        lv = self.query_one("#wlist", ListView)
        n = len(lv.children)
        if not n:
            return
        # Moving the highlight fires Highlighted → updates self.selected + the log pane.
        lv.index = ((lv.index or 0) + 1) % n

    def action_kill(self) -> None:
        if self.selected and self.app.controller.scheduler.kill_issue(self.selected):
            self.notify(f"kill signal sent to {self.selected}")
        else:
            self.notify("no running worker to kill", severity="warning")


class AttentionScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "resume", "Answer & resume"),
        Binding("tab", "next", "Next"),
    ]
    CSS = """
    #elist { height: 10; border: round $warning; }
    #answer { height: 8; border: round $accent; }
    """

    def __init__(self, slug: str):
        super().__init__()
        self.slug = slug
        self.selected: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Escalations needing your attention:")
        yield ListView(id="elist")
        yield Static(id="detail")
        yield Label("Your answer:")
        yield TextArea(id="answer")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_escs()

    def refresh_escs(self) -> None:
        escs = self.app.controller.escalations(self.slug)
        lv = self.query_one("#elist", ListView)
        lv.clear()
        for iid, reason in escs:
            lv.append(ListItem(Label(f"{iid}: {reason[:60]}"), name=iid))
        if escs and self.selected is None:
            self.selected = escs[0][0]
        self._show_detail()

    def _show_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        if not self.selected:
            detail.update("No escalations. 🎉")
            return
        path = self.app.controller.store.paths.escalation_file(self.slug, self.selected)
        detail.update(path.read_text() if path.exists() else f"{self.selected}: (no detail)")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        self.selected = event.item.name
        self._show_detail()

    def action_next(self) -> None:
        escs = [e[0] for e in self.app.controller.escalations(self.slug)]
        if not escs:
            return
        i = (escs.index(self.selected) + 1) % len(escs) if self.selected in escs else 0
        self.selected = escs[i]
        self._show_detail()

    def action_resume(self) -> None:
        if not self.selected:
            return
        answer = self.query_one("#answer", TextArea).text.strip()
        if not answer:
            self.notify("Type an answer first", severity="warning")
            return
        slug, iid = self.slug, self.selected

        async def task():
            try:
                await self.app.controller.resume(slug, iid, answer)
                self.notify(f"{iid} resumed")
            except Exception as e:
                self.notify(str(e), severity="error")
            self.refresh_escs()

        self.query_one("#answer", TextArea).text = ""
        self.app.run_worker(task(), exclusive=False)
        self.notify(f"resuming {iid}…")


class SettingsScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(id="cfg")
        yield Footer()

    def on_mount(self) -> None:
        import yaml
        cfg = self.app.controller.config
        text = yaml.safe_dump(cfg.to_dict(), sort_keys=False)
        path = self.app.controller.store.paths.config_file
        self.query_one("#cfg", Static).update(
            f"[b]Configuration[/b] (edit {path} directly; validated on load)\n\n{text}"
        )


class MetricsScreen(Screen):
    """WS6.1: success rate, mean retries/issue, cost/issue, escalation histogram, trends."""
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def __init__(self, slug: Optional[str]):
        super().__init__()
        self.slug = slug

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(id="metrics")
        yield Footer()

    def on_mount(self) -> None:
        c = self.app.controller
        parts = []
        if self.slug:
            parts.append(c.feature_metrics_text(self.slug))
        parts.append("")
        parts.append(c.metrics_trend_text())
        self.query_one("#metrics", Static).update("\n".join(parts))


class DashboardScreen(Screen):
    BINDINGS = [
        Binding("n", "new_feature", "New"),
        Binding("p", "planner", "Plan"),
        Binding("g", "grill", "Grill"),
        Binding("s", "slicer", "Slice"),
        Binding("c", "confirm_queue", "Confirm queue"),
        Binding("b", "build", "Build"),
        Binding("v", "review", "Review"),
        Binding("w", "workers", "Workers"),
        Binding("x", "attention", "Attention"),
        Binding("m", "metrics", "Metrics"),
        Binding("comma", "settings", "Settings"),
        Binding("q", "app.quit", "Quit"),
    ]
    CSS = """
    #left { width: 34; border-right: solid $accent; }
    #flist { height: 14; }
    #skills { height: auto; }
    #board { height: 1fr; }
    #hint { color: $accent; padding: 1 1; }
    #glog { height: 8; border-top: solid $accent; }
    #statusbar { height: 1; background: $boost; color: $text; padding: 0 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left"):
                yield Label("Features ([b]n[/b]ew)")
                yield ListView(id="flist")
                yield Static(id="skills")
            with Vertical():
                yield Static(id="hint")
                yield Static(id="board")
                with VerticalScroll(id="glog"):
                    yield Static(id="glogbody")
        yield Static(id="statusbar")   # persistent "what's happening now" line
        yield Footer()

    def on_mount(self) -> None:
        self._tick = 0
        self._build_feature_list()
        self.set_interval(0.2, self.refresh_status)  # fast tick → live spinner
        self.set_interval(0.4, self.refresh_data)
        self.refresh_data()
        self.refresh_status()

    # The status line ticks faster than the heavy data refresh so the spinner
    # animates smoothly and activity feels alive even mid-agent-run.
    def refresh_status(self) -> None:
        self._tick += 1
        c = self.app.controller
        sb = self.query_one("#statusbar", Static)
        spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        act = c.activity
        if act is not None and act.running:
            ch = spin[self._tick % len(spin)]
            sb.update(f"[b green]{ch} ACTIVE[/]  {c.status_line()}")
        else:
            last = c.global_log[-1] if c.global_log else "no activity yet — pick an action below"
            sb.update(f"[dim]● idle[/dim]  {last}")

    def _build_feature_list(self) -> None:
        lv = self.query_one("#flist", ListView)
        lv.clear()
        for slug in self.app.controller.features():
            lv.append(ListItem(Label(slug), name=slug))
        if self.app.current_slug is None and self.app.controller.features():
            self.app.current_slug = self.app.controller.features()[0]

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        self.app.current_slug = event.item.name
        self.refresh_data()

    def refresh_data(self) -> None:
        c = self.app.controller
        # Skills panel
        skills = self.query_one("#skills", Static)
        lines = ["[b]Vendored skills[/b]"]
        for s in c.skills_status():
            mark = "✓" if s.state.value == "ok" else "✗"
            lines.append(f" {mark} {s.name} v{s.packaged_version} [{s.state.value}]")
        missing = c.missing_required()
        if missing:
            lines.append(f"[red]⚠ missing: {', '.join(missing)}[/red]")
        lines.append("[b]Read-only agents[/b]")
        for a in c.agents_status():
            mark = "✓" if a.state.value == "ok" else "✗"
            lines.append(f" {mark} {a.name} v{a.packaged_version} [{a.state.value}]")
        skills.update("\n".join(lines))

        slug = self.app.current_slug
        hint = self.query_one("#hint", Static)
        board = self.query_one("#board", Static)
        glog = self.query_one("#glogbody", Static)
        glog.update("\n".join(c.global_log[-50:]))

        if not slug:
            hint.update("No feature selected. Press [b]n[/b] to create one.")
            board.update("")
            return
        st = c.feature(slug)
        cost = c.feature_cost(slug)
        n_workers = sum(1 for w in c.workers.values() if w.status == "running")
        hint_text = (
            f"[b]{slug}[/b] — phase: [b]{st.phase.value}[/b]   "
            f"cost: ${cost:.4f}   active workers: {n_workers}\n"
            + PHASE_HINT.get(st.phase, "")
        )
        if st.phase == Phase.QUEUE_REVIEW:  # WS4.1: show the conflict graph here
            summary = c.conflict_summary(slug)
            if summary:
                hint_text += "\n" + summary
        if st.phase in (Phase.PLAN_REVIEW, Phase.DOC_REVIEW):  # WS5.2 triage badges
            for kind in DOC_KINDS:
                d = st.doc(kind)
                if d and d.status.value in ("in_review", "changes_requested"):
                    hint_text += f"\n  {kind}: {c.review_badges(slug, kind)} [{d.status.value}]"
        hint.update(hint_text)
        board.update(self._kanban(st))

    def _kanban(self, st) -> Table:
        cols = [IssueStatus.QUEUED, IssueStatus.IN_PROGRESS, IssueStatus.TESTS_FAILING,
                IssueStatus.NEEDS_HUMAN, IssueStatus.DONE, IssueStatus.MERGED]
        table = Table(title="Issue board", expand=True)
        for col in cols:
            table.add_column(col.value, ratio=1)
        buckets = {col: [] for col in cols}
        for i in st.issues:
            buckets.setdefault(i.status, []).append(i.id)
        height = max((len(v) for v in buckets.values()), default=0)
        for r in range(height):
            row = [("\n".join(buckets[col][r:r+1]) if r < len(buckets[col]) else "") for col in cols]
            table.add_row(*row)
        if not st.issues:
            table.add_row(*["" for _ in cols])
        return table

    # ---- actions ----
    def _need_slug(self) -> Optional[str]:
        if not self.app.current_slug:
            self.notify("Select or create a feature first", severity="warning")
            return None
        return self.app.current_slug

    def action_new_feature(self) -> None:
        def done(result):
            if result:
                title, req = result
                slug = self.app.controller.create_feature(title, req)
                self.app.current_slug = slug
                self._build_feature_list()
                self.refresh_data()
                self.notify(f"created feature {slug}")
        self.app.push_screen(NewFeatureScreen(), done)

    def _run_phase(self, coro_factory, label: str) -> None:
        slug = self._need_slug()
        if not slug:
            return

        async def task():
            try:
                await coro_factory(slug)
                self.notify(f"{label} done")
            except Exception as e:
                self.notify(f"{label}: {e}", severity="error")
            self.refresh_data()

        self.notify(f"{label} running…")
        self.app.run_worker(task(), exclusive=False)

    def action_planner(self) -> None:
        self._run_phase(self.app.controller.run_planner, "planner")

    def action_grill(self) -> None:
        self._run_phase(self.app.controller.run_grill, "grill")

    def action_slicer(self) -> None:
        self._run_phase(self.app.controller.run_slicer, "slicer")

    def action_build(self) -> None:
        self._run_phase(self.app.controller.build, "build")

    def action_confirm_queue(self) -> None:
        slug = self._need_slug()
        if slug:
            self.app.controller.confirm_queue(slug)
            self.notify("queue confirmed — press b to build")
            self.refresh_data()

    def action_review(self) -> None:
        slug = self._need_slug()
        if slug:
            self.app.push_screen(ReviewScreen(slug))

    def action_workers(self) -> None:
        self.app.push_screen(WorkerScreen())

    def action_attention(self) -> None:
        slug = self._need_slug()
        if slug:
            self.app.push_screen(AttentionScreen(slug))

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def action_metrics(self) -> None:
        self.app.push_screen(MetricsScreen(self.app.current_slug))


class ForemanTUI(App):
    TITLE = "Foreman"
    SUB_TITLE = "agentic delivery orchestrator"
    CSS = """
    Screen { background: $surface; }
    """

    def __init__(self, repo_root: Path | str = ".", demo: bool = False):
        super().__init__()
        self.controller = Controller(repo_root, demo=demo)
        self.current_slug: Optional[str] = None

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())
        self.set_interval(0.5, self._bell_check)

    def _bell_check(self) -> None:
        if self.controller.bell_pending:
            self.controller.bell_pending = False
            self.bell()  # terminal bell on new escalations (§8.4)
