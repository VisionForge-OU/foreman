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
