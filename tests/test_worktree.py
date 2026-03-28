"""WorktreeManager — integration worktree must work when the user's repo is itself
checked out on the integration branch (the common case: repo sits on `main`)."""
import shutil
import subprocess

import pytest

from foreman import git_ops
from foreman.worktree import WorktreeManager, _base_dir


def _init(d, *, branch):
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "x@y.z"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "x"], cwd=d, check=True)
    (d / "f.txt").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, check=True)


@pytest.mark.asyncio
async def test_integration_worktree_uses_repo_when_on_integration_branch(tmp_path):
    repo = tmp_path / "repo"
    _init(repo, branch="main")
    wm = WorktreeManager(repo, "main")
    try:
        await wm.ensure_base()
        # Previously raised: fatal: 'main' is already used by worktree at <repo>.
        integ = await wm.integration_worktree()
        assert integ == repo                       # uses the user's checkout directly

        # An issue worktree still forks cleanly from main and removes safely.
        wt = await wm.create_issue_worktree("ISS-001", "feature/x/iss-001")
        assert wt != repo and wt.exists()
        await wm.remove(wt)
        assert not wt.exists()

        # Guard: removing the "integration worktree" must never delete the repo.
        await wm.remove(repo)
        assert repo.exists() and (repo / "f.txt").exists()
    finally:
        shutil.rmtree(_base_dir(repo), ignore_errors=True)


@pytest.mark.asyncio
async def test_integration_worktree_separate_when_off_integration_branch(tmp_path):
    repo = tmp_path / "repo"
    _init(repo, branch="work")                     # primary on a different branch
    wm = WorktreeManager(repo, "main")
    try:
        await wm.ensure_base()                     # creates `main` (unoccupied)
        integ = await wm.integration_worktree()
        assert integ != repo and integ.exists()
        assert await git_ops.current_branch(integ) == "main"
    finally:
        await wm.remove(_base_dir(repo) / "_integration")
        shutil.rmtree(_base_dir(repo), ignore_errors=True)
