"""Independent verification of a worker's claims (§7, §12).

Foreman re-runs the configured test/lint/typecheck commands ITSELF in the
worktree and blocks "done" on failure, regardless of what the agent's
FOREMAN-SUMMARY claims. This is the trust boundary.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CommandOutcome:
    name: str
    command: str
    ran: bool
    passed: Optional[bool]
    returncode: Optional[int]
    output_tail: str


@dataclass
class VerifyResult:
    outcomes: list[CommandOutcome] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """All commands that actually ran must have passed (and at least one ran)."""
        ran = [o for o in self.outcomes if o.ran]
        return bool(ran) and all(o.passed for o in ran)

    @property
    def failures(self) -> list[CommandOutcome]:
        return [o for o in self.outcomes if o.ran and not o.passed]

    def report(self) -> str:
        lines = []
        for o in self.outcomes:
            if not o.ran:
                lines.append(f"- {o.name}: skipped (not configured)")
            else:
                flag = "PASS" if o.passed else "FAIL"
                lines.append(f"- {o.name} [{flag}] `{o.command}`")
        return "\n".join(lines)

    def failure_output(self) -> str:
        return "\n\n".join(
            f"### {o.name} failed (`{o.command}`)\n{o.output_tail}" for o in self.failures
        )


async def _run_command(
    name: str, command: str, cwd: Path, timeout_s: float,
    env: Optional[dict] = None,
) -> CommandOutcome:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=run_env,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandOutcome(name, command, True, False, None, "timed out")
        text = out.decode(errors="replace")
        tail = "\n".join(text.splitlines()[-40:])
        return CommandOutcome(name, command, True, proc.returncode == 0, proc.returncode, tail)
    except FileNotFoundError as e:
        return CommandOutcome(name, command, True, False, None, f"command not found: {e}")


async def verify(
    cwd: Path,
    commands: dict[str, Optional[str]],
    *,
    names: tuple[str, ...] = ("test", "lint", "typecheck"),
    timeout_s: float = 600.0,
    env: Optional[dict] = None,
) -> VerifyResult:
    """Run the named commands in ``cwd``. Absent/empty commands are skipped.

    ``env`` overlays the subprocess environment (Phase-2: the worktree-hook PATH +
    ``FOREMAN_TEST_CMD`` so Foreman's verification run resolves ``foreman-test``).
    """
    result = VerifyResult()
    for name in names:
        command = commands.get(name)
        command = (command or "").strip() if command else ""
        if not command:
            result.outcomes.append(CommandOutcome(name, "", False, None, None, ""))
            continue
        result.outcomes.append(await _run_command(name, command, cwd, timeout_s, env=env))
    return result


def outcome_by_name(result: VerifyResult, name: str) -> Optional[CommandOutcome]:
    for o in result.outcomes:
        if o.name == name:
            return o
    return None
