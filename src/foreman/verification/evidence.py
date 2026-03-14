"""The completion-evidence contract (WS1.3).

A worker's "complete" claim is only honoured if it is backed by real evidence
artifacts (test logs, command outputs, screenshots for UI work) saved under
``runs/<id>/evidence/``. Foreman validates the evidence **on disk** — a complete
summary with missing or empty evidence is rejected and treated as a failed
attempt. The artifact list in the FOREMAN-SUMMARY is cross-checked but the disk
is the source of truth (workers cannot fabricate the list past this gate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EvidenceResult:
    ok: bool
    artifacts: list[str] = field(default_factory=list)   # non-empty files on disk
    claimed: list[str] = field(default_factory=list)     # from the summary
    missing: list[str] = field(default_factory=list)     # claimed but absent/empty
    reason: str = ""

    def report(self) -> str:
        if self.ok:
            return f"evidence OK: {len(self.artifacts)} artifact(s) — {', '.join(self.artifacts)}"
        return f"evidence MISSING: {self.reason}"


def _nonempty_artifacts(evidence_dir: Path) -> list[str]:
    if not evidence_dir.exists():
        return []
    out: list[str] = []
    for f in sorted(evidence_dir.rglob("*")):
        try:
            if f.is_file() and f.stat().st_size > 0:
                out.append(str(f.relative_to(evidence_dir)))
        except OSError:
            continue
    return out


def validate(evidence_dir: Path | str, claimed: list[str] | None = None) -> EvidenceResult:
    """Validate the completion evidence for a run.

    Passes iff there is at least one non-empty artifact on disk under
    ``evidence_dir`` AND every artifact the summary *claimed* is present and
    non-empty. An empty/absent evidence dir always fails (WS1.3).
    """
    evidence_dir = Path(evidence_dir)
    claimed = [c for c in (claimed or []) if str(c).strip()]
    artifacts = _nonempty_artifacts(evidence_dir)

    if not artifacts:
        return EvidenceResult(
            ok=False, artifacts=[], claimed=claimed,
            missing=list(claimed),
            reason=f"no non-empty evidence artifacts under {evidence_dir}",
        )

    by_name = {Path(a).name: a for a in artifacts}
    missing = [
        c for c in claimed
        if Path(c).name not in by_name and c not in artifacts
    ]
    if missing:
        return EvidenceResult(
            ok=False, artifacts=artifacts, claimed=claimed, missing=missing,
            reason=f"claimed evidence not found on disk: {', '.join(missing)}",
        )
    return EvidenceResult(ok=True, artifacts=artifacts, claimed=claimed, missing=[])
