"""The ``notify_command`` runner — review-needed / escalation pings (P2.3 WS5).

When ``notify_command`` is configured, Foreman fires it on review-needed and
escalation events with the feature / doc / issue id and a one-line reason. The
payload is exposed BOTH as appended shell args AND as ``FOREMAN_*`` env vars so a
hook can consume whichever is convenient. Strictly best-effort: a missing config,
a bad command, or a slow command never raises and never blocks the scheduler.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

_TIMEOUT_SEC = 15

# Env var names the payload is published under for the notify subprocess.
ENV_EVENT = "FOREMAN_EVENT"
ENV_FEATURE = "FOREMAN_FEATURE"
ENV_REF = "FOREMAN_REF"
ENV_REASON = "FOREMAN_REASON"


async def notify(
    notify_command: Optional[str],
    *,
    event: str,
    feature: str,
    ref: str,
    reason: str,
) -> bool:
    """Fire ``notify_command`` (a shell string) with the event payload.

    The payload is passed both as appended quoted args (``<cmd> <event> <feature>
    <ref> <reason>``) and as ``FOREMAN_EVENT/FEATURE/REF/REASON`` env vars.

    Best-effort: returns True on a clean exit (code 0), False on anything else
    (unset/blank command, non-zero exit, timeout, spawn failure). Never raises.
    """
    if not notify_command or not str(notify_command).strip():
        return False

    env = dict(os.environ)
    env[ENV_EVENT] = str(event)
    env[ENV_FEATURE] = str(feature)
    env[ENV_REF] = str(ref)
    env[ENV_REASON] = str(reason)

    # Append the payload as positional args too, shell-quoted for safety.
    import shlex
    appended = " ".join(
        shlex.quote(str(x)) for x in (event, feature, ref, reason)
    )
    full = f"{notify_command} {appended}"

    try:
        proc = await asyncio.create_subprocess_shell(
            full,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return False

    try:
        await asyncio.wait_for(proc.wait(), timeout=_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return False
    return proc.returncode == 0


def fire(
    notify_command: Optional[str],
    *,
    event: str,
    feature: str,
    ref: str,
    reason: str,
) -> bool:
    """Sync convenience wrapper around :func:`notify`.

    Safe to call from non-async code: if no event loop is running it drives one to
    completion; if a loop is already running it schedules the coroutine
    fire-and-forget and returns True (callers inside async code should ``await
    notify`` directly). Best-effort — never raises.
    """
    coro = notify(
        notify_command, event=event, feature=feature, ref=ref, reason=reason
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # Already inside an event loop — schedule and don't block it.
        loop.create_task(coro)
        return True
    try:
        return asyncio.run(coro)
    except Exception:
        return False
