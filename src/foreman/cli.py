"""``foreman`` command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__, vendored
from .vendored import SkillState


def _cmd_init(args) -> int:
    from .installer import init_repo

    result = init_repo(Path(args.path), force=args.force)
    print(f"Initialized Foreman in {result['root']}")
    print(f"  config: {'created' if result['config_created'] else 'already present'}"
          f"  ({Path(result['root']) / '.foreman' / 'config.yaml'})")
    if result["skills_installed"]:
        print(f"  installed/updated skills: {', '.join(result['skills_installed'])}")
    else:
        print("  skills: already up to date")
    for s in result["skills_status"]:
        print(f"    - {s.name}: v{s.packaged_version} [{s.state.value}]")
    if result.get("agents_installed"):
        print(f"  installed/updated agents: {', '.join(result['agents_installed'])}")
    for a in result.get("agents_status", []):
        print(f"    - {a.name}: v{a.packaged_version} [{a.state.value}]")
    print("\nNext: run `foreman` to launch the TUI, or `foreman demo` to see it work.")
    return 0


def _cmd_status(args) -> int:
    from .paths import RepoPaths
    from .state import FileStore

    repo = Path(args.path)
    paths = RepoPaths(repo)
    if not paths.is_initialized():
        print(f"Not initialized: {repo}\nRun `foreman init` here first.")
        return 1
    print(f"Foreman repo: {paths.root}")
    print("\nVendored skills:")
    any_missing = False
    for s in vendored.status(repo):
        marker = "✓" if s.state == SkillState.OK else "✗"
        if s.state == SkillState.MISSING:
            any_missing = True
        print(f"  {marker} {s.name}: installed={s.installed_version} packaged={s.packaged_version}"
              f" [{s.state.value}]")
    from .agents import installer as agents_installer
    print("\nRead-only agents:")
    for a in agents_installer.status(repo):
        marker = "✓" if a.state.value == "ok" else "✗"
        print(f"  {marker} {a.name}: installed={a.installed_version} "
              f"packaged={a.packaged_version} [{a.state.value}]")
    store = FileStore(repo)
    print("\nFeatures:")
    slugs = store.list_features()
    if not slugs:
        print("  (none yet)")
    for slug in slugs:
        st = store.load_feature(slug)
        n_iss = len(st.issues)
        print(f"  • {slug} — phase={st.phase.value} issues={n_iss}")
    if any_missing:
        print("\n⚠ Some required skills are missing. Run `foreman init` to install them.")
    return 0


def _cmd_demo(args) -> int:
    from .demo import run_demo

    target = Path(args.path) if args.path else None
    fail = None if args.no_fail else "ISS-001"
    asyncio.run(run_demo(target, fail_first_issue=fail, on_log=print))
    print("\nDemo complete. Explore the generated .foreman/ tree to see all state on disk.")
    return 0


def _cmd_run(args) -> int:
    from .paths import RepoPaths
    from .headless import run_feature, HeadlessError
    from .tui.controller import Controller

    repo = Path(args.path)
    if not RepoPaths(repo).is_initialized():
        print(f"Not initialized: {repo}\nRun `foreman init` here first.")
        return 1
    if not args.auto_approve:
        print("`foreman run` is non-interactive and needs --auto-approve "
              "(it bypasses the human review gate). Use the TUI for gated review.")
        return 1

    request = args.request
    if args.request_file:
        request = Path(args.request_file).read_text()

    controller = Controller(repo, demo=False)
    if args.model:
        controller.config.model_planner = args.model
        controller.config.model_worker = args.model
    if args.no_e2e:
        controller.config.e2e_enabled = False

    try:
        slug, report = asyncio.run(
            run_feature(controller, args.title, request or "", auto_approve=True, on_log=print)
        )
    except HeadlessError as e:
        print(f"\nhalted: {e}")
        return 2
    escalated = bool(report.escalated)
    print(f"\nfeature {slug} finished — "
          f"{len(report.merged)} merged, {len(report.escalated)} escalated, "
          f"${report.total_cost_usd:.4f}")
    return 1 if escalated else 0


def _cmd_build(args) -> int:
    from .paths import RepoPaths
    from .models import IssueStatus
    from .tui.controller import Controller

    repo = Path(args.path)
    if not RepoPaths(repo).is_initialized():
        print(f"Not initialized: {repo}\nRun `foreman init` here first.")
        return 1
    controller = Controller(repo, demo=False)
    if args.model:
        controller.config.model_worker = args.model
    controller.log_sink = print

    slugs = controller.features()
    slug = args.slug or (slugs[0] if len(slugs) == 1 else None)
    if slug is None:
        print(f"Specify --slug; features: {', '.join(slugs) or '(none)'}")
        return 1

    store = controller.store
    state = store.load_feature(slug)
    # Optionally raise per-issue budgets (the slicer can size them too small for a
    # large repo) and requeue escalated issues for another attempt.
    for issue in state.issues:
        changed = False
        if args.budget_turns and issue.budget.max_turns < args.budget_turns:
            issue.budget.max_turns = args.budget_turns
            changed = True
        if args.retry_escalated and issue.status in (
            IssueStatus.NEEDS_HUMAN, IssueStatus.TESTS_FAILING,
        ):
            issue.status = IssueStatus.QUEUED
            issue.attempts = 0
            changed = True
        if changed:
            store.write_issue(slug, issue)

    print(f"• resuming build of {slug}…")
    report = asyncio.run(controller.build(slug))
    print("\n" + report.render())
    return 1 if report.escalated else 0


def _cmd_retro(args) -> int:
    """Cluster recurring failures and draft gated skill/rubric/prompt patches (WS6.2)."""
    from .paths import RepoPaths
    from .retro import driver, metrics
    from .tui.controller import Controller

    repo = Path(args.path)
    if not RepoPaths(repo).is_initialized():
        print(f"Not initialized: {repo}\nRun `foreman init` here first.")
        return 1
    controller = Controller(repo, demo=False)
    store = controller.store
    slugs = [args.slug] if args.slug else store.list_features()
    if not slugs:
        print("No features yet — nothing to retro.")
        return 0
    for slug in slugs:
        print(metrics.render(metrics.load_feature_metrics(store, slug)))
        print()
    if args.list:
        for p in sorted(store.paths.retro_dir.glob("*.md")) if store.paths.retro_dir.exists() else []:
            sp = driver.load(store, p.stem)
            print(f"  {sp.name}: {sp.proposal.target} [{sp.status}] — {sp.proposal.title}")
        return 0
    print("• analysing failure clusters and drafting patch proposals…")
    proposals, clusters, _ = asyncio.run(
        driver.analyze(store, controller.config, controller.backend, slugs=slugs)
    )
    for c in clusters:
        print(f"  [{c.count}×] {c.pattern}")
    names = driver.draft(store, proposals)
    if names:
        print(f"\nDrafted {len(names)} patch proposal(s) (status=in_review): {', '.join(names)}")
        print("Each needs human approval AND a `foreman bench` report before it can land.")
    else:
        print("\nNo patch proposals were drafted.")
    return 0


def _cmd_bench(args) -> int:
    """Replay the eval set and report success-rate/cost/turn deltas (WS6.3)."""
    from .paths import RepoPaths
    from .retro import bench, driver
    from .tui.controller import Controller

    repo = Path(args.path)
    if not RepoPaths(repo).is_initialized():
        print(f"Not initialized: {repo}\nRun `foreman init` here first.")
        return 1
    controller = Controller(repo, demo=False)
    store = controller.store
    eval_dir = Path(args.eval_set) if args.eval_set else (repo / controller.config.bench_eval_set)
    cases = bench.load_eval_set(eval_dir)
    if not cases:
        print(f"No eval set at {eval_dir}. Seed one with past runs first.")
        return 1
    ceiling = None if args.real else None  # mocked default spends nothing

    async def factory(case):
        # Mocked default: replay the case's recorded baseline outcome (no tokens).
        # Real-token replay through the scheduler is opt-in (--real) and bounded by
        # config.bench_cost_ceiling_usd.
        return {"outcome": case.expected_outcome, "cost_usd": 0.0, "turns": 0}

    if args.real:
        ceiling = controller.config.bench_cost_ceiling_usd
        print(f"• real-token bench (ceiling ${ceiling:.2f})")
    report = asyncio.run(bench.run_bench(
        cases, runner_factory=factory, mocked=not args.real, cost_ceiling_usd=ceiling,
    ))
    print(report.render())
    if args.proposal:
        path = driver.attach_bench(store, args.proposal, report)
        print(f"• attached bench report to proposal {args.proposal}: {path}")
    return 0


def _cmd_tui(args) -> int:
    from .paths import RepoPaths

    repo = Path(args.path)
    if not args.demo and not RepoPaths(repo).is_initialized():
        print(f"Not initialized: {repo}\nRun `foreman init` here first, or use `foreman --demo`.")
        return 1
    try:
        from .tui.app import ForemanTUI
    except Exception as e:  # pragma: no cover - import/runtime guard
        print(f"Could not start the TUI: {e}", file=sys.stderr)
        return 1
    ForemanTUI(repo_root=repo, demo=args.demo).run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="foreman",
        description="Agentic orchestrator TUI that supervises headless Claude Code "
                    "agents through a gated plan→ADR/PRD→issues→TDD→e2e pipeline.",
    )
    p.add_argument("--version", action="version", version=f"foreman {__version__}")
    p.add_argument("--demo", action="store_true",
                   help="launch the TUI against a throwaway sample repo with mocked agents")
    sub = p.add_subparsers(dest="command")

    pi = sub.add_parser("init", help="scaffold .foreman/ and install the foreman-* skills")
    pi.add_argument("path", nargs="?", default=".")
    pi.add_argument("--force", action="store_true", help="overwrite config and reinstall skills")
    pi.set_defaults(func=_cmd_init)

    ps = sub.add_parser("status", help="show skill + feature status for a repo")
    ps.add_argument("path", nargs="?", default=".")
    ps.set_defaults(func=_cmd_status)

    pd = sub.add_parser("demo", help="run the full pipeline against a sample repo (mocked agents)")
    pd.add_argument("path", nargs="?", default=None)
    pd.add_argument("--no-fail", action="store_true",
                    help="don't inject a first-attempt failure")
    pd.set_defaults(func=_cmd_demo)

    pr = sub.add_parser("run", help="run a feature through the whole pipeline headlessly "
                                    "(non-interactive; bypasses the review gate)")
    pr.add_argument("path", nargs="?", default=".")
    pr.add_argument("--title", required=True, help="feature title")
    pr.add_argument("--request", default="", help="feature request text")
    pr.add_argument("--request-file", default=None, help="read request text from a file")
    pr.add_argument("--auto-approve", action="store_true",
                    help="required: auto-approve gates once open questions are resolved")
    pr.add_argument("--model", default=None, help="override model for planner and workers")
    pr.add_argument("--no-e2e", action="store_true", help="skip the e2e phase")
    pr.set_defaults(func=_cmd_run)

    pb = sub.add_parser("build", help="resume/continue the autonomous build of an existing feature")
    pb.add_argument("path", nargs="?", default=".")
    pb.add_argument("--slug", default=None, help="feature slug (default: the only feature)")
    pb.add_argument("--retry-escalated", action="store_true",
                    help="requeue needs_human/tests_failing issues for another attempt")
    pb.add_argument("--budget-turns", type=int, default=None,
                    help="raise each issue's max_turns to at least this value")
    pb.add_argument("--model", default=None, help="override the worker model")
    pb.set_defaults(func=_cmd_build)

    prt = sub.add_parser("retro", help="cluster recurring failures and draft gated skill/prompt patches")
    prt.add_argument("path", nargs="?", default=".")
    prt.add_argument("--slug", default=None, help="limit to one feature (default: all)")
    prt.add_argument("--list", action="store_true", help="list existing proposals and exit")
    prt.set_defaults(func=_cmd_retro)

    pbn = sub.add_parser("bench", help="replay the eval set; report success-rate/cost/turn deltas")
    pbn.add_argument("path", nargs="?", default=".")
    pbn.add_argument("--eval-set", default=None, help="eval-set dir (default: config.bench_eval_set)")
    pbn.add_argument("--real", action="store_true", help="real-token mode (default: mocked)")
    pbn.add_argument("--proposal", default=None, help="attach the report to this retro proposal")
    pbn.set_defaults(func=_cmd_bench)

    pt = sub.add_parser("tui", help="launch the TUI (default when no command given)")
    pt.add_argument("path", nargs="?", default=".")
    pt.add_argument("--demo", action="store_true")
    pt.set_defaults(func=_cmd_tui)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        # No subcommand → launch the TUI on the current directory.
        ns = argparse.Namespace(path=".", demo=getattr(args, "demo", False), command="tui")
        return _cmd_tui(ns)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
