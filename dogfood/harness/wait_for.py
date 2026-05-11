"""``wait_for`` — drive the Textual event loop while polling disk truth.

Loops ``await pilot.pause(poll)`` (yielding to the app + its Textual workers, which
is where real ``claude`` subprocesses make progress) and re-evaluates a predicate
that reads ``.foreman/`` state. Generous *real-time* timeouts (no clock mocking —
workers take minutes). On timeout we raise ``WaitTimeout`` so the conductor can
snapshot the stuck state and record a finding instead of hanging forever.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable


class WaitTimeout(Exception):
    def __init__(self, desc: str, elapsed: float):
        super().__init__(f"timed out after {elapsed:.0f}s waiting for: {desc}")
        self.desc = desc
        self.elapsed = elapsed


async def wait_for(pilot, predicate: Callable[[], bool], *, timeout: float,
                   poll: float = 0.75, desc: str = "") -> float:
    """Return elapsed seconds once ``predicate()`` is truthy; raise on timeout."""
    start = time.monotonic()
    deadline = start + timeout
    # Evaluate once up front (predicate may already hold).
    if predicate():
        return 0.0
    while time.monotonic() < deadline:
        await pilot.pause(poll)
        try:
            if predicate():
                return time.monotonic() - start
        except Exception:
            # A half-written state file mid-poll — ignore and re-poll.
            pass
    raise WaitTimeout(desc, time.monotonic() - start)


async def wait_until_idle(pilot, controller, *, timeout: float, poll: float = 0.75,
                          desc: str = "agent idle") -> float:
    """Wait until Foreman reports no running activity and no running workers.

    ``controller.activity`` is set by ``begin_activity`` and cleared by
    ``end_activity``; a build/phase worker is idle when it's None and no worker
    log is in the ``running`` state.
    """
    def idle() -> bool:
        if controller.activity is not None and getattr(controller.activity, "running", False):
            return False
        return not any(w.status == "running" for w in controller.workers.values())

    return await wait_for(pilot, idle, timeout=timeout, poll=poll, desc=desc)
