"""Conflict-aware scheduling from declared footprints (WS4.1).

Each issue declares a ``touches`` footprint (files/dirs/modules). The scheduler
builds a conflict graph from footprint overlap and never co-schedules two
overlapping issues, biasing dispatch toward maximum parallel width. An issue with
an **unknown** (empty) footprint conflicts with everything — the safe default for
Phase-1-migrated or under-specified issues (P2.2) — so it runs alone.

Footprint overlap is path-containment: ``src/`` overlaps ``src/a.py``; ``a/b.py``
overlaps ``a/b.py``; ``a/`` does not overlap ``b/``.
"""

from __future__ import annotations

from typing import Iterable

from .models import Issue


def _norm(path: str) -> str:
    return path.strip().strip("/").replace("\\", "/")


def _paths_overlap(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return True  # an empty path means "everything"
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def footprints_overlap(a: Iterable[str], b: Iterable[str]) -> bool:
    a, b = list(a), list(b)
    if not a or not b:
        return True  # unknown footprint conflicts with everything (safe default)
    return any(_paths_overlap(pa, pb) for pa in a for pb in b)


def issues_conflict(a: Issue, b: Issue) -> bool:
    if a.id == b.id:
        return False
    return footprints_overlap(a.touches, b.touches)


def conflict_graph(issues: list[Issue]) -> dict[str, set[str]]:
    """Undirected conflict adjacency keyed by issue id (for the queue-review view)."""
    graph: dict[str, set[str]] = {i.id: set() for i in issues}
    for idx, a in enumerate(issues):
        for b in issues[idx + 1:]:
            if issues_conflict(a, b):
                graph[a.id].add(b.id)
                graph[b.id].add(a.id)
    return graph


def pick_dispatch(ready: list[Issue], running: list[Issue], max_new: int) -> list[Issue]:
    """Choose a conflict-free set of ready issues to start now (greedy, max width).

    Never returns an issue that conflicts with a currently-running issue or with
    another issue chosen in the same round. An unknown-footprint issue only runs
    when nothing else is (or will be) running this round.
    """
    if max_new <= 0:
        return []
    # If any running issue has an unknown footprint, it conflicts with all — wait.
    if any(not r.footprint_known for r in running):
        return []

    # Prefer known-footprint issues, then the least-conflicting, then id order, to
    # maximise the number that can run together.
    degree = {i.id: 0 for i in ready}
    for idx, a in enumerate(ready):
        for b in ready[idx + 1:]:
            if issues_conflict(a, b):
                degree[a.id] += 1
                degree[b.id] += 1
    ordered = sorted(ready, key=lambda i: (not i.footprint_known, degree[i.id], i.id))

    chosen: list[Issue] = []
    for issue in ordered:
        if len(chosen) >= max_new:
            break
        if not issue.footprint_known:
            # Runs strictly alone.
            if not running and not chosen:
                chosen.append(issue)
            continue
        if any(issues_conflict(issue, r) for r in running):
            continue
        if any(issues_conflict(issue, c) for c in chosen):
            continue
        chosen.append(issue)
    return chosen
