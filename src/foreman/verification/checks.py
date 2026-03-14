"""Executable acceptance checks (WS1.1).

Every issue ships a *runnable* acceptance check — the executable form of its
acceptance criteria, derived by the slicer from the PRD. The check is the
``acceptance_check`` frontmatter field plus an optional canonical artifact tree
under ``issues/ISS-NNN.check/`` (a test file or check script). An issue cannot
enter the build queue without a check (WS1.1).

The check is either:
- a **command** (anything containing whitespace, or not resolving to a file):
  run verbatim in the worktree, e.g. ``pytest tests/test_done.py::test_mark``; or
- a **test file** (a bare path that resolves to a file in the ``.check/`` dir or
  the worktree): run as ``<configured test command> <path>``.

Anti-tamper: the canonical ``.check/`` artifacts are re-installed into the
worktree right before verification, so a worker cannot weaken or delete the test
to make the gate pass.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import verify
from ..models import Issue


@dataclass
class AcceptanceCheck:
    spec: str                 # the raw acceptance_check string ("" if absent)
    check_dir: Optional[Path] = None  # canonical issues/ISS-NNN.check/ if it exists

    @property
    def present(self) -> bool:
        return bool(self.spec.strip())

    @property
    def is_command(self) -> bool:
        """A command (vs a bare test-file path). Whitespace ⇒ command."""
        return bool(self.spec.strip()) and len(self.spec.split()) > 1

    @classmethod
    def for_issue(cls, issue: Issue, check_dir: Optional[Path] = None) -> "AcceptanceCheck":
        cd = check_dir if (check_dir and Path(check_dir).is_dir()) else None
        return cls(spec=(issue.acceptance_check or "").strip(), check_dir=cd)

    def install_into(self, worktree: Path) -> list[str]:
        """Copy canonical ``.check/`` artifacts into the worktree (anti-tamper).

        Files land at the same relative path the slicer chose under ``.check/``
        (e.g. ``.check/tests/test_x.py`` → ``<worktree>/tests/test_x.py``).
        Returns the relative paths installed.
        """
        if self.check_dir is None:
            return []
        installed: list[str] = []
        for src in sorted(self.check_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(self.check_dir)
            dest = Path(worktree) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            installed.append(str(rel))
        return installed

    def command(self, commands: dict[str, Optional[str]]) -> Optional[str]:
        """Resolve the shell command that runs this check, or None if absent."""
        if not self.present:
            return None
        if self.is_command:
            return self.spec
        # Bare path → run the configured test command against it.
        test_cmd = (commands.get("test") or "").strip()
        if not test_cmd:
            return self.spec  # no test runner configured; best-effort run the path
        return f"{test_cmd} {self.spec}"

    async def run(
        self, worktree: Path, commands: dict[str, Optional[str]], *,
        timeout_s: float = 600.0, env: Optional[dict] = None,
    ) -> verify.CommandOutcome:
        """Re-install canonical artifacts, then run the acceptance check."""
        self.install_into(worktree)
        cmd = self.command(commands)
        if not cmd:
            return verify.CommandOutcome("acceptance", "", False, None, None, "")
        return await verify._run_command("acceptance", cmd, Path(worktree), timeout_s, env=env)


def issues_missing_checks(issues: list[Issue]) -> list[str]:
    """Ids of non-janitor issues lacking a runnable acceptance check (WS1.1 gate)."""
    return [
        i.id for i in issues
        if not i.is_janitor and not (i.acceptance_check or "").strip()
    ]
