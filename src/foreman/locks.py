"""Crash-safe task locks (WS4.2) — the second line of defence after footprints.

Declared footprints (WS4.1) can be wrong, so a worker also takes a per-issue lock
before it works. Locks are files under ``current_tasks/ISS-XXX.lock`` in the
**integration worktree** (on the integration branch's working tree, kept out of
git via the repo's local exclude — see ``git_ops.ensure_excluded``). Each lock
carries a heartbeat; a lock whose heartbeat is older than the TTL is *stale* and
reclaimable (dead-worker detection), making double-claiming impossible across
crashes/restarts.

v1 deviation (documented): with no git remote we cannot use push-conflict
detection, so cross-process safety comes from this shared on-disk lock dir +
heartbeat reclaim. The protocol is remote-ready (the lock is on the integration
branch) for when a remote exists.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

DEFAULT_TTL_S = 900.0  # 15 min without a heartbeat ⇒ the worker is presumed dead


def _now() -> float:
    return time.time()


def lock_dir(integ_wt: Path | str) -> Path:
    return Path(integ_wt) / "current_tasks"


def lock_path(integ_wt: Path | str, issue_id: str) -> Path:
    return lock_dir(integ_wt) / f"{issue_id}.lock"


@dataclass
class Lock:
    issue_id: str
    run_id: str
    heartbeat: float
    started: float


def read_lock(integ_wt: Path | str, issue_id: str) -> Optional[Lock]:
    path = lock_path(integ_wt, issue_id)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text())
        return Lock(issue_id=str(d["issue_id"]), run_id=str(d.get("run_id", "")),
                    heartbeat=float(d.get("heartbeat", 0.0)), started=float(d.get("started", 0.0)))
    except (json.JSONDecodeError, ValueError, KeyError, OSError):
        return None


def _write(integ_wt: Path | str, lock: Lock) -> None:
    d = lock_dir(integ_wt)
    d.mkdir(parents=True, exist_ok=True)
    lock_path(integ_wt, lock.issue_id).write_text(json.dumps({
        "issue_id": lock.issue_id, "run_id": lock.run_id,
        "heartbeat": lock.heartbeat, "started": lock.started,
    }, indent=2))


def is_stale(lock: Lock, *, now: Optional[float] = None, ttl_s: float = DEFAULT_TTL_S) -> bool:
    now = _now() if now is None else now
    return (now - lock.heartbeat) > ttl_s


def acquire(
    integ_wt: Path | str, issue_id: str, *, run_id: str,
    now: Optional[float] = None, ttl_s: float = DEFAULT_TTL_S,
) -> bool:
    """Take the lock. Fails iff a *live* lock held by a different run exists."""
    now = _now() if now is None else now
    existing = read_lock(integ_wt, issue_id)
    if existing is not None and existing.run_id != run_id and not is_stale(existing, now=now, ttl_s=ttl_s):
        return False  # held by a live worker — back off
    _write(integ_wt, Lock(issue_id=issue_id, run_id=run_id, heartbeat=now, started=now))
    return True


def heartbeat(integ_wt: Path | str, issue_id: str, *, run_id: str, now: Optional[float] = None) -> None:
    now = _now() if now is None else now
    existing = read_lock(integ_wt, issue_id)
    started = existing.started if existing else now
    _write(integ_wt, Lock(issue_id=issue_id, run_id=run_id, heartbeat=now, started=started))


def release(integ_wt: Path | str, issue_id: str) -> None:
    path = lock_path(integ_wt, issue_id)
    if path.exists():
        path.unlink()


def active(integ_wt: Path | str) -> dict[str, Lock]:
    d = lock_dir(integ_wt)
    if not d.exists():
        return {}
    out: dict[str, Lock] = {}
    for p in d.glob("*.lock"):
        lk = read_lock(integ_wt, p.stem)
        if lk is not None:
            out[lk.issue_id] = lk
    return out


def reclaim_stale(
    integ_wt: Path | str, *, now: Optional[float] = None, ttl_s: float = DEFAULT_TTL_S
) -> list[str]:
    """Remove locks whose worker is presumed dead. Returns the reclaimed issue ids."""
    now = _now() if now is None else now
    reclaimed: list[str] = []
    for issue_id, lock in active(integ_wt).items():
        if is_stale(lock, now=now, ttl_s=ttl_s):
            release(integ_wt, issue_id)
            reclaimed.append(issue_id)
    return reclaimed
