"""The AgentBackend seam (R1, §2) — the highest seam in Foreman.

``AgentBackend.run(spec)`` yields :class:`StreamEvent`s. Everything above this
seam (pipeline, scheduler, TUI) is backend-agnostic and therefore testable
without burning tokens.

- :class:`ClaudeBackend` spawns the real ``claude`` CLI in headless stream-json
  mode (R1) and parses its output line by line.
- :class:`MockBackend` replays scripted responses (events + canned file side
  effects) so the whole state machine and TUI can be exercised offline (§11.5).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional, Protocol

from .models import Budget
from .stream_parser import StreamEvent, parse_line


@dataclass
class RunSpec:
    """Everything needed to launch one agent run."""

    kind: str                       # planner | grill | slicer | tdd | e2e | revise
    slug: str
    repo_root: Path
    cwd: Path                       # worktree or repo root
    prompt: str
    model: str
    effort: str
    permission_mode: str
    budget: Budget
    label: str = ""                 # run label, e.g. "ISS-001" or "planner"
    extra_dirs: list[Path] = field(default_factory=list)
    session_id: Optional[str] = None  # for --resume on escalation answers (§7)
    # Phase-2: extra settings file (worktree hooks) + env overlay (P2.3 WS1.3).
    settings_path: Optional[Path] = None
    env: dict[str, str] = field(default_factory=dict)
    # Phase-2: a named subagent to run as (--agent), e.g. the read-only evaluator.
    agent: Optional[str] = None

    def argv(self) -> list[str]:
        """Build the ``claude`` command line (single source of truth for flags)."""
        argv = [
            "claude", "-p", self.prompt,
            "--output-format", "stream-json",
            "--verbose",                       # required for the full event stream
            "--model", self.model,
            "--effort", self.effort,
            "--permission-mode", self.permission_mode,
        ]
        if self.agent:
            argv += ["--agent", self.agent]
        if self.settings_path is not None:
            argv += ["--settings", str(self.settings_path)]
        # Native cost ceiling, belt-and-suspenders with Foreman's own enforcement.
        if self.budget.max_cost_usd and self.budget.max_cost_usd > 0:
            argv += ["--max-budget-usd", f"{self.budget.max_cost_usd}"]
        for d in self.extra_dirs:
            argv += ["--add-dir", str(d)]
        if self.session_id:
            argv += ["--resume", self.session_id]
        return argv


class AgentBackend(Protocol):
    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        ...


class ClaudeBackend:
    """Spawns the real ``claude`` CLI (R1).

    We deliberately do NOT pass ``--strict-mcp-config`` or strip the user's
    settings: workers run with the user's normal environment so their other
    installed skills remain available (R2). cwd is the target repo / worktree.
    """

    def __init__(self, executable: str = "claude"):
        self.executable = executable

    async def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        if not Path(spec.cwd).is_dir():
            raise RuntimeError(
                f"working directory does not exist: {spec.cwd} "
                "(worktree creation may have failed)"
            )
        argv = spec.argv()
        argv[0] = self.executable
        env = os.environ.copy()
        env.update(spec.env or {})  # worktree-hook PATH + FOREMAN_TEST_CMD etc. (WS1.3)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(spec.cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                event = parse_line(raw.decode("utf-8", errors="replace"))
                if event is not None:
                    yield event
        finally:
            # Killing the worker must leave nothing running (§7).
            if proc.returncode is None:
                try:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                except ProcessLookupError:
                    pass


# --------------------------------------------------------------------------- #
# Mock backend
# --------------------------------------------------------------------------- #
# A script is an async callable: (spec) -> async-iterable of StreamEvent. It may
# perform canned file side effects (writing the docs/issues/code a real agent
# would have produced) before/while yielding events.
Script = Callable[[RunSpec], AsyncIterator[StreamEvent]]


class MockBackend:
    """Replays scripted responses keyed by spec.kind (and optionally label).

    Used by ``foreman demo`` and the test suite. Construct with a registry mapping
    a key to a :data:`Script`. Lookup order: ``"{kind}:{label}"`` then ``kind``.
    """

    def __init__(self, scripts: dict[str, Script], *, step_delay: float = 0.0):
        self.scripts = scripts
        self.step_delay = step_delay

    async def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        script = self.scripts.get(f"{spec.kind}:{spec.label}") or self.scripts.get(spec.kind)
        if script is None:
            raise KeyError(f"MockBackend has no script for kind={spec.kind!r} label={spec.label!r}")
        async for event in script(spec):
            if self.step_delay:
                await asyncio.sleep(self.step_delay)
            yield event
