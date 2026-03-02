"""Content hashing for the approval gate (R3).

An approval records a SHA-256 of the document *body* at approval time. On every
load we recompute the body hash; a mismatch means the document changed after
approval, so the approval is invalid and the status reverts to in_review.

We hash the body only (never the frontmatter) because the frontmatter is exactly
where the approval block itself lives — including it would make every approval
self-invalidating.
"""

from __future__ import annotations

import hashlib


def body_hash(body: str) -> str:
    """Return the SHA-256 hex digest of a document body, newline-normalized.

    Normalizing line endings and trailing whitespace means an editor that
    rewrites CRLF or trims a trailing blank line does not spuriously invalidate
    an approval.
    """
    normalized = body.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
