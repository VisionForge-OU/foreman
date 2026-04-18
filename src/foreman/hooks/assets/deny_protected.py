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

It also covers **MCP tools**: a worker that follows the user's environment may
edit via an MCP tool (e.g. lean-ctx ``ctx_edit`` → ``{path, old_string, new_string}``)
or run shells via an MCP tool (``ctx_shell`` → ``{command}``) instead of the native
Edit/Bash. Matching only by native tool *name* would let those bypass the gate, so
we match by tool-input *shape* — any tool that writes a file (a path key plus an
edit/content marker, or a write-ish tool name) or runs a command — not by name.
Read-only tools (a path with no write markers) are left alone.
"""

import json
import re
import sys

PROTECTED_BASENAMES = ("verification.json", "baseline.json")
_ISSUE_PATH = re.compile(r"[\\/]\.foreman[\\/]features[\\/][^\\/]+[\\/]issues[\\/]")
_CHECK_PATH = re.compile(r"\.check[\\/]")

# tool_input keys that name a file target (native + common MCP shapes).
_PATH_KEYS = ("file_path", "path", "notebook_path", "filePath", "filepath", "target", "file")
# tool_input keys that signal the call WRITES content — so a path here is a write,
# not a read (this is how we tell ctx_edit from ctx_read: both carry ``path``).
_WRITE_MARKER_KEYS = (
    "old_string", "new_string", "content", "contents", "text", "data", "patch", "edits",
)
# Substrings in a tool name that indicate it mutates a file (for MCP tools).
_WRITE_NAME_HINTS = (
    "edit", "write", "apply", "patch", "create", "save", "replace", "insert",
    "append", "delete", "remove", "move", "rename", "mkdir", "truncate",
)
# tool_input keys that carry a shell command (native Bash + MCP shells).
_CMD_KEYS = ("command", "cmd", "script")


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


def _is_write_tool(tool: str, ti: dict) -> bool:
    """Does this tool WRITE a file (vs read it)? Covers native edit tools and MCP
    edit tools (ctx_edit etc.). A bare path with no write markers (a read) is not."""
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return True
    low = str(tool).lower()
    if any(h in low for h in _WRITE_NAME_HINTS):
        return True
    return any(k in ti for k in _WRITE_MARKER_KEYS)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # never block on a malformed event
    tool = event.get("tool_name", "")
    ti = event.get("tool_input", {}) or {}
    if not isinstance(ti, dict):
        return 0

    # 1. Any command-bearing tool (native Bash OR an MCP shell like ctx_shell):
    #    block if the command writes a protected file.
    for ck in _CMD_KEYS:
        cmd = ti.get(ck)
        if isinstance(cmd, str):
            token = _bash_touches_protected(cmd)
            if token:
                sys.stderr.write(
                    f"Foreman: that command writes a Foreman-owned file ({token}); "
                    "it is blocked. Foreman flips verification itself.\n"
                )
                return 2

    # 2. Any file-WRITING tool (native Write/Edit/… OR an MCP edit like ctx_edit):
    #    block if its target path is Foreman-owned. Reads are left alone.
    if _is_write_tool(tool, ti):
        for pk in _PATH_KEYS:
            p = ti.get(pk)
            if isinstance(p, str) and _is_protected(p):
                _deny(f"Foreman: workers may not write {p} — Foreman owns it.")
                return 0
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
