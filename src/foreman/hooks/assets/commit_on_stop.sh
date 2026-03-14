#!/usr/bin/env bash
# Foreman Stop hook — git backstop so a killed/stopped worker never loses work
# (cwc-long-running-agents primitive, P2.0/WS3.2). Best-effort: stages and commits
# any uncommitted changes in the worktree with a WIP message. Never blocks.
set -e
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  git add -A 2>/dev/null || true
  git -c user.name=Foreman -c user.email=foreman@localhost \
      commit -m "wip: foreman stop-hook checkpoint" --no-verify >/dev/null 2>&1 || true
fi
exit 0
