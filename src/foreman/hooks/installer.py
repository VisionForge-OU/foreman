"""Install Foreman's per-worktree hooks and the ``foreman-test`` wrapper (WS1.3/1.5).

To avoid polluting the worktree's tracked tree (which would show as a diff and
could be committed), Foreman writes its hook scripts and a ``settings.json`` into
a **sibling** directory next to the worktree (``<worktree>.foreman-hooks/``) and
loads them with ``claude --settings <that>/settings.json`` — additive to the
user's own settings, so their MCP/skills still load (R2). The returned
:class:`HookInstall` tells the runner what ``--settings`` file to pass and what
environment to inject (PATH for ``foreman-test`` + ``FOREMAN_TEST_CMD`` +
``FOREMAN_WORKER_ID``).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ASSETS = Path(__file__).resolve().parent / "assets"
# Native file/command tools PLUS every MCP tool (mcp__*): a worker may follow the
# user's environment and edit/run via MCP equivalents (lean-ctx ctx_edit / ctx_shell
# instead of Edit / Bash), so the deny hook must see those too. (Verified: this regex
# fires the PreToolUse hook for mcp__lean-ctx__ctx_shell.) deny_protected.py then
# decides per call — MCP reads and unprotected writes are allowed.
_FILE_MATCHER = "Write|Edit|MultiEdit|NotebookEdit|Bash|mcp__.*"


@dataclass
class HookInstall:
    settings_path: Path
    hooks_dir: Path
    env: dict[str, str] = field(default_factory=dict)


def _chmod_x(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install(
    worktree: Path | str,
    *,
    test_command: Optional[str] = None,
    worker_id: str = "default",
    test_log: Optional[str] = None,
) -> HookInstall:
    """Install hooks + foreman-test alongside ``worktree``; return runner wiring."""
    worktree = Path(worktree).resolve()
    hooks_dir = worktree.parent / f"{worktree.name}.foreman-hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    # Default the test log to live OUTSIDE the worktree (no diff pollution).
    if test_log is None:
        test_log = str(hooks_dir / "foreman-test.log")

    deny = hooks_dir / "deny_protected.py"
    stop = hooks_dir / "commit_on_stop.sh"
    ftest = hooks_dir / "foreman-test"
    shutil.copy2(ASSETS / "deny_protected.py", deny)
    shutil.copy2(ASSETS / "commit_on_stop.sh", stop)
    shutil.copy2(ASSETS / "foreman-test", ftest)
    for f in (deny, stop, ftest):
        _chmod_x(f)

    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": _FILE_MATCHER,
                 "hooks": [{"type": "command", "command": f"python3 {deny}"}]},
            ],
            "Stop": [
                {"matcher": "*",
                 "hooks": [{"type": "command", "command": f"bash {stop}"}]},
            ],
        }
    }
    settings_path = hooks_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    env = {"PATH": f"{hooks_dir}{os.pathsep}{os.environ.get('PATH', '')}",
           "FOREMAN_WORKER_ID": worker_id,
           "FOREMAN_TEST_LOG": test_log}
    if test_command:
        env["FOREMAN_TEST_CMD"] = test_command
    return HookInstall(settings_path=settings_path, hooks_dir=hooks_dir, env=env)


def cleanup(worktree: Path | str) -> None:
    """Remove the sibling hooks dir (best-effort)."""
    worktree = Path(worktree).resolve()
    hooks_dir = worktree.parent / f"{worktree.name}.foreman-hooks"
    if hooks_dir.exists():
        shutil.rmtree(hooks_dir, ignore_errors=True)
