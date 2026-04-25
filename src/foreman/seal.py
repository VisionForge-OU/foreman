"""The hash-seal — the one place the approval invariant lives (R3, DECISIONS §4).

A *seal* binds a reviewer's approval to the exact body they approved: a SHA-256
fingerprint of the normalized body. On every load, a stored fingerprint that no
longer matches the current body means the body changed after approval, so the
seal is broken and the approval must revert to in_review.

Two adapters cross this seam — gated documents (plan/adr/prd, via
:class:`~foreman.state.FileStore`) and retro proposals (``retro/driver.py``) — so
the predicate lives here rather than being re-implemented in each. The low-level
digest stays in :mod:`foreman.hashing`; ``fingerprint`` delegates to it.
"""

from __future__ import annotations

from .hashing import body_hash


def fingerprint(body: str) -> str:
    """Canonical seal fingerprint of a document body."""
    return body_hash(body)


def intact(stored_fingerprint: str | None, body: str) -> bool:
    """True iff a seal was recorded AND still matches the current body.

    A missing/empty stored fingerprint is never intact (nothing was sealed).
    """
    return bool(stored_fingerprint) and stored_fingerprint == fingerprint(body)
