"""`foreman demo` — run the entire pipeline against a sample repo with a mocked
agent backend (§11.5, §12). No tokens are spent; this exercises the full state
machine, gates, scheduler, worktrees, independent verification and e2e.

Because there is no human in a demo, the gate decisions are scripted: the plan is
auto-approved, the first PRD draft (which carries an open question) is sent back
with an answer to demonstrate the revision loop, the revised docs are approved,
the queue is confirmed, and the build runs autonomously.
"""

from __future__ import annotations

import itertools
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .backend import MockBackend
from .config import Config
from .demo_scripts import demo_scripts
from .installer import init_repo
from .ledger import CostLedger
from .pipeline import Pipeline
from .sample import create_sample_repo, pytest_command
from .scheduler import Scheduler
from .state import FileStore


def _demo_config() -> Config:
    cfg = Config()
    cfg.commands = {"test": pytest_command(), "lint": "", "typecheck": "", "e2e": pytest_command()}
    cfg.e2e_enabled = True
    cfg.stuck_turns = 0
    cfg.limits.max_parallel = 2
    return cfg


async def run_demo(
    target_dir: Optional[Path] = None,
    *,
    fail_first_issue: Optional[str] = "ISS-001",
    on_log: Callable[[str], None] = print,
):
    """Run the full demo. Returns (slug, BuildReport, repo_path)."""
    if target_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="foreman-demo-"))
    repo = create_sample_repo(Path(target_dir) / "todo-cli")
    on_log(f"• scaffolded sample repo at {repo}")

    init_repo(repo)
    on_log("• foreman init: scaffolded .foreman/ and installed foreman-* skills")

    counter = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    cfg = _demo_config()
    rc = itertools.count(1)

    class _Log:
        def log(self, m): on_log("  " + m)
        def worker_started(self, i, r): on_log(f"  ▶ worker {i} ({r})")
        def worker_event(self, i, e): pass
        def worker_finished(self, i, s, res): on_log(f"  ■ worker {i}: {s} (${res.record.cost_usd:.4f})")
        def escalated(self, i, reason): on_log(f"  ⚠ {i} escalated: {reason}")

    scripts = demo_scripts(fail_first_issue=fail_first_issue)
    backend = MockBackend(scripts)
    pipe = Pipeline(store, cfg, backend, run_id_clock=lambda: f"r{next(rc):04d}")

    slug = store.create_feature(
        "Add done command to todo CLI",
        "Users want to mark a todo item complete via `todo done <id>`.",
    )
    on_log(f"• feature created: {slug}")

    # --- Phase A: gated pipeline ---
    await pipe.run_planner(slug)
    on_log("• planner produced plan.md (in_review)")
    store.approve_doc(slug, "plan", "demo-reviewer")
    on_log("• plan APPROVED")

    await pipe.run_grill(slug)
    prd = store.load_feature(slug).doc("prd")
    on_log(f"• grill produced adr.md + prd.md (prd has {len(prd.open_questions)} open question)")
    store.request_changes(
        slug, "prd", "demo-reviewer",
        "Re-completing an already-done item should be a silent no-op.",
    )
    on_log("• reviewer answered the open question (request changes)")
    await pipe.run_grill(slug)
    on_log("• grill revised drafts (open questions resolved)")
    store.approve_doc(slug, "adr", "demo-reviewer")
    store.approve_doc(slug, "prd", "demo-reviewer")
    on_log("• ADR + PRD APPROVED")

    await pipe.run_slicer(slug)
    issues = store.load_feature(slug).issues
    on_log(f"• slicer produced {len(issues)} issues: {', '.join(i.id for i in issues)}")
    store.confirm_queue(slug)
    on_log("• queue CONFIRMED — last gate passed")

    # --- Phase B: autonomous build ---
    on_log("• starting autonomous build loop…")
    sched = Scheduler(
        store, cfg, backend,
        ledger=CostLedger(store.paths.daily_cost_file),
        monitor=_Log(), run_id_clock=lambda: f"b{next(rc):04d}",
    )
    report = await sched.build(slug)
    on_log("")
    on_log(report.render())
    return slug, report, repo
