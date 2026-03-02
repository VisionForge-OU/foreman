"""Thin async wrappers over the ``git`` CLI (§3, §7).

Foreman uses git worktrees so multiple issue workers can run in parallel on
separate branches without colliding. All git access goes through here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Scoped identity for Foreman's own commits when the repo has none configured.
_IDENTITY = ["-c", "user.name=Foreman", "-c", "user.email=foreman@localhost"]


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def git(repo: Path | str, *args: str, identity: bool = False) -> GitResult:
    argv = ["git", "-C", str(repo)]
    if identity:
        argv += _IDENTITY
    argv += list(args)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return GitResult(proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))


async def is_repo(repo: Path | str) -> bool:
    res = await git(repo, "rev-parse", "--is-inside-work-tree")
    return res.ok and res.stdout.strip() == "true"


async def current_branch(repo: Path | str) -> str:
    res = await git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return res.stdout.strip()


async def head_sha(repo: Path | str) -> str:
    res = await git(repo, "rev-parse", "HEAD")
    return res.stdout.strip()


async def ensure_initial_commit(repo: Path | str) -> None:
    """Make sure the repo has at least one commit (worktrees need a base)."""
    res = await git(repo, "rev-parse", "HEAD")
    if not res.ok:
        await git(repo, "add", "-A")
        await git(repo, "commit", "--allow-empty", "-m", "initial", identity=True)


async def add_worktree(repo: Path | str, path: Path | str, branch: str, base: str) -> GitResult:
    """Create a worktree at ``path`` on a new ``branch`` forked from ``base``.

    If the branch already exists (a retry), check it out instead of creating it.
    """
    exists = await git(repo, "rev-parse", "--verify", branch)
    if exists.ok:
        return await git(repo, "worktree", "add", str(path), branch)
    return await git(repo, "worktree", "add", "-b", branch, str(path), base)


async def remove_worktree(repo: Path | str, path: Path | str) -> GitResult:
    """Remove a worktree, discarding any uncommitted changes in it (§7 rollback)."""
    return await git(repo, "worktree", "remove", "--force", str(path))


async def prune_worktrees(repo: Path | str) -> GitResult:
    """Drop registrations for worktrees whose directories no longer exist.

    Stale registrations (e.g. left by a killed process that shared the temp
    worktree namespace) otherwise make ``git worktree add`` fail. Pruning first
    makes worktree creation idempotent across runs/processes.
    """
    return await git(repo, "worktree", "prune")


async def discard_changes(worktree: Path | str) -> None:
    """Roll a worktree back to a clean state (used when killing a worker)."""
    await git(worktree, "reset", "--hard")
    await git(worktree, "clean", "-fd")


async def commit_all(worktree: Path | str, message: str) -> GitResult:
    await git(worktree, "add", "-A")
    return await git(worktree, "commit", "-m", message, identity=True)


async def has_uncommitted(worktree: Path | str) -> bool:
    res = await git(worktree, "status", "--porcelain")
    return bool(res.stdout.strip())


async def merge_branch(
    repo: Path | str, branch: str, *, strategy: str = "merge", message: Optional[str] = None,
) -> GitResult:
    """Merge ``branch`` into the currently checked-out integration branch."""
    if strategy == "squash":
        res = await git(repo, "merge", "--squash", branch)
        if res.ok:
            return await git(repo, "commit", "-m", message or f"merge {branch} (squash)", identity=True)
        return res
    if strategy == "rebase":
        return await git(repo, "rebase", branch)
    return await git(repo, "merge", "--no-ff", "-m", message or f"merge {branch}", branch, identity=True)


async def diff_stat(worktree: Path | str) -> str:
    res = await git(worktree, "diff", "--stat", "HEAD")
    return res.stdout.strip()
