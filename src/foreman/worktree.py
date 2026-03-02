"""Git worktree management for parallel issue workers (§3, §7).

Worktrees live outside the target repo tree (under the system temp dir, keyed by
repo + slug) so they never nest inside the working copy or get committed. Each
issue gets its own worktree on its own branch; an "integration" worktree holds
the integration branch where merges land, so the user's main checkout is never
disturbed.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from . import git_ops


def _base_dir(repo_root: Path) -> Path:
    key = hashlib.sha1(str(Path(repo_root).resolve()).encode()).hexdigest()[:10]
    return Path(tempfile.gettempdir()) / "foreman-worktrees" / key


class WorktreeManager:
    def __init__(self, repo_root: Path | str, integration_branch: str = "main"):
        self.repo_root = Path(repo_root).resolve()
        self.integration_branch = integration_branch

    def _path(self, name: str) -> Path:
        return _base_dir(self.repo_root) / name

    async def ensure_base(self) -> None:
        """Ensure the repo has a commit and the integration branch exists."""
        await git_ops.ensure_initial_commit(self.repo_root)
        # Drop stale registrations from prior/killed processes before anything else.
        await git_ops.prune_worktrees(self.repo_root)
        exists = await git_ops.git(self.repo_root, "rev-parse", "--verify", self.integration_branch)
        if not exists.ok:
            await git_ops.git(self.repo_root, "branch", self.integration_branch)

    async def integration_worktree(self) -> Path:
        """Create (or reuse) the worktree holding the integration branch."""
        path = self._path("_integration")
        path.parent.mkdir(parents=True, exist_ok=True)
        await git_ops.prune_worktrees(self.repo_root)
        check = await git_ops.git(self.repo_root, "worktree", "list", "--porcelain")
        if str(path) in check.stdout and path.exists():
            return path
        # Registration without a backing dir (or vice-versa): clear and recreate.
        await self.remove(path)
        res = await git_ops.git(self.repo_root, "worktree", "add", str(path), self.integration_branch)
        if not res.ok or not path.exists():
            raise RuntimeError(
                f"failed to create integration worktree at {path}: {res.stderr.strip()}"
            )
        return path

    async def create_issue_worktree(self, issue_id: str, branch: str) -> Path:
        """Fork a fresh worktree for an issue from the integration branch."""
        path = self._path(issue_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Clean any stale registration/dir from a previous run, then prune.
        await self.remove(path)
        await git_ops.prune_worktrees(self.repo_root)
        res = await git_ops.add_worktree(
            self.repo_root, path, branch, base=self.integration_branch
        )
        # Verify the worktree actually exists — never hand back a missing cwd,
        # which would surface downstream as an opaque FileNotFoundError.
        if not res.ok or not path.exists():
            raise RuntimeError(
                f"failed to create worktree for {issue_id} (branch {branch}) "
                f"at {path}: {res.stderr.strip() or 'unknown git error'}"
            )
        return path

    async def remove(self, path: Path) -> None:
        check = await git_ops.git(self.repo_root, "worktree", "list", "--porcelain")
        if str(path) in check.stdout:
            await git_ops.remove_worktree(self.repo_root, path)
        if path.exists():
            import shutil
            shutil.rmtree(path, ignore_errors=True)

    async def rollback_and_remove(self, path: Path) -> None:
        """Discard all changes in a worktree then remove it (kill cleanup, §7)."""
        if path.exists():
            await git_ops.discard_changes(path)
        await self.remove(path)
