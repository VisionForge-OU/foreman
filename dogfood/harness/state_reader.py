"""Disk-truth readers — the robust synchronization path (goal Part A.3).

The harness drives the TUI for *interaction* but waits on and asserts against the
``.foreman/`` state files for *progress*. Everything here reads a
``foreman.state.FileStore`` (or the raw escalation files) so it survives TUI
layout changes and is decoupled from widget internals.
"""
from __future__ import annotations

import re
from pathlib import Path

from foreman.models import DocStatus, IssueStatus, Phase
from foreman.state import FileStore

DONE_STATES = (IssueStatus.DONE, IssueStatus.MERGED)


def feature(store: FileStore, slug: str):
    return store.load_feature(slug)


def phase(store: FileStore, slug: str) -> Phase:
    return store.load_feature(slug).phase


def doc_drafted(store: FileStore, slug: str, kind: str) -> bool:
    return store.load_feature(slug).doc(kind) is not None


def doc_status(store: FileStore, slug: str, kind: str):
    d = store.load_feature(slug).doc(kind)
    return d.status if d else None


def doc_approved(store: FileStore, slug: str, kind: str) -> bool:
    return doc_status(store, slug, kind) == DocStatus.APPROVED


def plan_drafted(store: FileStore, slug: str) -> bool:
    return doc_drafted(store, slug, "plan")


def docs_drafted(store: FileStore, slug: str) -> bool:
    st = store.load_feature(slug)
    return st.doc("adr") is not None and st.doc("prd") is not None


def docs_approved(store: FileStore, slug: str) -> bool:
    return doc_approved(store, slug, "adr") and doc_approved(store, slug, "prd")


def issues_sliced(store: FileStore, slug: str) -> bool:
    return len([i for i in store.load_feature(slug).issues if not i.is_janitor]) > 0


def queue_confirmed(store: FileStore, slug: str) -> bool:
    return store.load_feature(slug).queue_confirmed


def feature_issues(store: FileStore, slug: str):
    return store.load_feature(slug).issues


def all_issues_done(store: FileStore, slug: str) -> bool:
    issues = [i for i in store.load_feature(slug).issues if not i.is_janitor]
    return bool(issues) and all(i.status in DONE_STATES for i in issues)


def feature_done(store: FileStore, slug: str) -> bool:
    return store.load_feature(slug).phase == Phase.DONE


def issue_status_counts(store: FileStore, slug: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in store.load_feature(slug).issues:
        counts[i.status.value] = counts.get(i.status.value, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Escalations — pure parse helper (unit-tested) + a disk lister.
# --------------------------------------------------------------------------- #
_ESC_HEADING = re.compile(r"^##\s+Escalation\b", re.MULTILINE)
_ANS_HEADING = re.compile(r"^##\s+Answer\b", re.MULTILINE)


def escalation_open(text: str) -> bool:
    """True if the escalation file's *last* escalation has no answer after it.

    The on-disk format appends ``## Escalation @ <ts>`` blocks (each followed by a
    ``<!-- Reviewer: add your answer ... -->`` marker) and answers are appended as
    ``## Answer @ <ts>`` blocks. An escalation is waiting iff the final
    ``## Escalation`` heading is not followed by an ``## Answer`` heading.
    """
    if not text or not _ESC_HEADING.search(text):
        return False
    last_esc = list(_ESC_HEADING.finditer(text))[-1].start()
    return not _ANS_HEADING.search(text, last_esc)


def open_escalations(store: FileStore, slug: str) -> list[str]:
    """Issue ids with an unanswered escalation on disk (decoupled from scheduler)."""
    base = Path(store.paths.feature_dir(slug)) / "escalations"
    if not base.is_dir():
        return []
    out = []
    for f in sorted(base.glob("*.md")):
        if escalation_open(f.read_text()):
            out.append(f.stem)
    return out
