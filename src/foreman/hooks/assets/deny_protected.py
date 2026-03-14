#!/usr/bin/env python3
"""Foreman PreToolUse hook — deny worker writes to Foreman-owned state (WS1.3).

Installed into a worktree's ``.claude/`` by ``foreman.hooks.installer``. Runs as a
standalone subprocess (NO foreman import — it must work in whatever environment
``claude`` spawns hooks in). Receives the PreToolUse event as JSON on stdin.

Protected targets a worker may never write:
- any ``verification.json`` (the Foreman-owned structural-done map — WS1.2);
- any ``baseline.json`` (the regression ratchet baseline — WS1.4);
- any issue file under ``.foreman/features/*/issues/`` (the issue ``status`` field
  and the canonical ``*.check/`` acceptance artifacts live here).

For file tools (Write/Edit/MultiEdit/NotebookEdit) it emits a structured
``permissionDecision: deny`` (proven to block even under ``acceptEdits``). For
Bash it inspects the command for redirects/moves into a protected file and, if
found, blocks via exit code 2 with the reason on stderr (also proven).
"""

import json
import re
import sys

PROTECTED_BASENAMES = ("verification.json", "baseline.json")
_ISSUE_PATH = re.compile(r"[\\/]\.foreman[\\/]features[\\/][^\\/]+[\\/]issues[\\/]")
_CHECK_PATH = re.compile(r"\.check[\\/]")


def _is_protected(path: str) -> bool:
    if not path:
        return False
    base = path.replace("\\", "/").rstrip("/").split("/")[-1]
    if base in PROTECTED_BASENAMES:
        return True
    if _ISSUE_PATH.search(path):
        return True
    if _CHECK_PATH.search(path):
        return True
    return False


def _bash_touches_protected(command: str) -> str:
    """Return the protected token a bash command appears to write, or ''."""
    if not command:
        return ""
    # Look for protected basenames anywhere a write could land (redirect, tee,
    # mv/cp/rm targets). Conservative: any mention next to a write verb blocks.
    write_verbs = (">", ">>", "tee ", "mv ", "cp ", "rm ", "truncate", "dd ")
    has_write = any(v in command for v in write_verbs)
    for token in re.split(r"\s+", command):
        if _is_protected(token) and (has_write or token.startswith((">", ">>"))):
            return token
    # Also catch `... > path/verification.json` where the redirect glues to path.
    for base in PROTECTED_BASENAMES:
        if base in command and has_write:
            return base
    return ""


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # never block on a malformed event
    tool = event.get("tool_name", "")
    ti = event.get("tool_input", {}) or {}

    if tool in ("Write", "Edit", "MultiEdit"):
        path = ti.get("file_path") or ti.get("path") or ""
        if _is_protected(path):
            _deny(f"Foreman: workers may not write {path} — Foreman owns it.")
            return 0
    elif tool == "NotebookEdit":
        if _is_protected(ti.get("notebook_path", "")):
            _deny("Foreman: workers may not write Foreman-owned state.")
            return 0
    elif tool == "Bash":
        token = _bash_touches_protected(ti.get("command", ""))
        if token:
            sys.stderr.write(
                f"Foreman: that command writes a Foreman-owned file ({token}); "
                "it is blocked. Foreman flips verification itself.\n"
            )
            return 2
    return 0


def _deny(reason: str) -> None:
    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }},
        sys.stdout,
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    sys.exit(main())
