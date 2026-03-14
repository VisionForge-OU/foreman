"""The single authority that reads and writes a feature's ``verification.json``
(P2.2, WS1.2). Foreman is the ONLY writer; workers are hook-blocked at runtime.

Shape on disk (one entry per issue id)::

    {
      "ISS-001": {"passes": true,  "evidence": ["runs/<id>/evidence/test.log"],
                  "verified_at": "2026-...Z", "verified_by": "foreman"},
      "ISS-002": {"passes": false, "evidence": [], "verified_at": null,
                  "verified_by": null}
    }

"Done" is Foreman flipping ``passes`` here after its own checks — never the
worker's say-so (R/§7, WS1.2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..models import IssueVerification


def read(path: Path | str) -> dict[str, IssueVerification]:
    """Load the verification map. Missing/corrupt ⇒ empty map (tolerant, R4)."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, IssueVerification] = {}
    for issue_id, entry in raw.items():
        out[str(issue_id)] = IssueVerification.from_dict(
            entry if isinstance(entry, dict) else None
        )
    return out


def write(path: Path | str, verification: dict[str, IssueVerification]) -> None:
    """Atomically persist the verification map (Foreman-only writer)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v.to_dict() for k, v in sorted(verification.items())}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def seed_missing(
    path: Path | str, issue_ids: list[str], *, passed_ids: Optional[set[str]] = None
) -> dict[str, IssueVerification]:
    """Ensure every issue id has an entry (Default-FAIL), preserving existing ones.

    ``passed_ids`` (e.g. already-MERGED issues during migration) seed ``passes:true``
    with an empty-evidence migration note so the ratchet has a baseline (P2.2).
    Returns the resulting map (also written to disk).
    """
    passed_ids = passed_ids or set()
    current = read(path)
    for issue_id in issue_ids:
        if issue_id in current:
            continue
        if issue_id in passed_ids:
            current[issue_id] = IssueVerification(
                passes=True, evidence=[], verified_at=None, verified_by="migration"
            )
        else:
            current[issue_id] = IssueVerification()
    write(path, current)
    return current


def set_passed(
    path: Path | str,
    issue_id: str,
    *,
    evidence: list[str],
    verified_at: str,
    verified_by: str = "foreman",
) -> dict[str, IssueVerification]:
    """Flip one issue to passing with its evidence (the ONLY way "done" is set)."""
    current = read(path)
    current[issue_id] = IssueVerification(
        passes=True, evidence=list(evidence), verified_at=verified_at, verified_by=verified_by
    )
    write(path, current)
    return current


def set_failed(path: Path | str, issue_id: str) -> dict[str, IssueVerification]:
    """Reset one issue to Default-FAIL (e.g. on a bounce/regression)."""
    current = read(path)
    current[issue_id] = IssueVerification()
    write(path, current)
    return current
