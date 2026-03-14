"""Foreman worktree hooks + the foreman-test wrapper (P2.3 WS1.3/1.5).

``installer.install(worktree, ...)`` writes a PreToolUse deny hook (Foreman-owned
state is unwritable by workers), a Stop git-backstop hook, and the foreman-test
runner alongside a worktree, and returns the ``--settings`` file + environment the
runner injects. The deny mechanism is empirically proven to block writes even
under ``acceptEdits`` (DECISIONS.md §P2.0).
"""

from __future__ import annotations

from .installer import HookInstall, cleanup, install

__all__ = ["HookInstall", "install", "cleanup"]
