"""FileStore — the only state of record (R4).

All durable Foreman state is human-readable files under a target repo's
``.foreman/`` tree. This module reads and writes them and rebuilds the in-memory
:class:`FeatureState` from disk, so killing Foreman and restarting fully recovers
state. It also enforces the approval-invalidation rule (R3): if a gated document's
body changed since it was approved, the approval is dropped and status reverts to
``in_review`` *at load time* — making approval a pure function of file contents.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import frontmatter
from . import seal
from .models import (
    Approval,
    Budget,
    DocStatus,
    GatedDoc,
    FeatureState,
    Issue,
    IssueStatus,
    IssueVerification,
    Phase,
    Review,
    RunRecord,
    DOC_KINDS,
    ISSUE_KIND_FEATURE,
    SCHEMA_VERSION,
)
from .paths import RepoPaths, slugify
from .verification import verification_json


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _issue_status(value: object) -> IssueStatus:
    """Tolerant issue-status parse: an unknown value degrades to QUEUED rather
    than crashing a load (forward-compat for trees written by a newer Foreman)."""
    try:
        return IssueStatus(str(value))
    except ValueError:
        return IssueStatus.QUEUED


def _doc_status(value: object) -> DocStatus:
    """Tolerant doc-status parse (R4). A document-producing agent can transiently
    write its OWN frontmatter (e.g. ``status: draft``) before Foreman re-stamps the
    canonical status, and the TUI reads doc files on an interval — possibly mid-write.
    Never crash the load: an unknown value degrades to DRAFTING (a non-approved state),
    so a malformed/in-flight file can never read as approved or stall the whole UI."""
    try:
        return DocStatus(str(value).strip().lower())
    except ValueError:
        return DocStatus.DRAFTING


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class FileStore:
    """Reads/writes the ``.foreman/`` tree for a single target repo."""

    def __init__(self, repo_root: Path | str, clock: Callable[[], str] = _utcnow_iso):
        self.paths = RepoPaths(repo_root)
        self._clock = clock
        self._migrated = False

    # ------------------------------------------------------------------ #
    # Schema version & additive v1→v2 migration (P2.2)
    # ------------------------------------------------------------------ #
    def schema_version(self) -> int:
        """The on-disk schema version. Absent marker ⇒ a Phase-1 (v1) tree."""
        f = self.paths.schema_version_file
        if not f.exists():
            return 1
        try:
            return int(f.read_text().strip() or "1")
        except (ValueError, OSError):
            return 1

    def _stamp_schema_version(self, version: int = SCHEMA_VERSION) -> None:
        self.paths.foreman_dir.mkdir(parents=True, exist_ok=True)
        self.paths.schema_version_file.write_text(f"{version}\n")

    def ensure_migrated(self) -> None:
        """Idempotently bring a v1 tree up to v2 — purely additive (P2.2).

        Never rewrites or deletes a Phase-1 file. It only: stamps the version and
        seeds a Default-FAIL ``verification.json`` per feature (already-landed
        issues seeded ``passes:true`` so the regression ratchet has a baseline).
        Issue ``kind``/``touches`` default in-memory (feature / unknown-footprint)
        so old issue files load untouched.
        """
        if self._migrated:
            return
        if not self.paths.foreman_dir.exists():
            self._migrated = True
            return
        if self.schema_version() >= SCHEMA_VERSION:
            self._migrated = True
            return
        for slug in self.paths.feature_slugs():
            issues = self._load_issues(slug)
            if not issues:
                continue
            passed = {
                i.id for i in issues
                if i.status in (IssueStatus.DONE, IssueStatus.MERGED)
            }
            verification_json.seed_missing(
                self.paths.verification_file(slug),
                [i.id for i in issues],
                passed_ids=passed,
            )
        self._stamp_schema_version()
        self._migrated = True

    # ------------------------------------------------------------------ #
    # Feature lifecycle
    # ------------------------------------------------------------------ #
    def create_feature(self, title: str, request_body: str) -> str:
        """Create a feature directory and write ``request.md``. Returns the slug."""
        slug = slugify(title)
        fdir = self.paths.feature_dir(slug)
        fdir.mkdir(parents=True, exist_ok=True)
        self.paths.reviews_dir(slug).mkdir(exist_ok=True)
        self.paths.issues_dir(slug).mkdir(exist_ok=True)
        self.paths.runs_dir(slug).mkdir(exist_ok=True)
        meta = {"title": title, "created": self._clock()}
        self.paths.request_file(slug).write_text(
            frontmatter.serialize(meta, request_body)
        )
        # New trees are born at the current schema version (P2.2).
        if not self.paths.schema_version_file.exists():
            self._stamp_schema_version()
        return slug

    def list_features(self) -> list[str]:
        return self.paths.feature_slugs()

    def load_feature(self, slug: str) -> FeatureState:
        """Rebuild a feature's full state from disk (R4)."""
        self.ensure_migrated()
        state = FeatureState(slug=slug)
        req = self.paths.request_file(slug)
        if req.exists():
            doc = frontmatter.parse(req.read_text())
            state.request = doc.body
        for kind in DOC_KINDS:
            gd = self._load_doc(slug, kind)
            if gd is not None:
                state.docs[kind] = gd
        state.issues = self._load_issues(slug)
        state.queue_confirmed = self._queue_confirmed(slug)
        state.verification = verification_json.read(self.paths.verification_file(slug))
        state.phase = self._derive_phase(state)
        return state

    # ------------------------------------------------------------------ #
    # Verification map (Foreman-owned structural "done" — P2.2/WS1.2)
    # ------------------------------------------------------------------ #
    def verification(self, slug: str) -> dict[str, IssueVerification]:
        return verification_json.read(self.paths.verification_file(slug))

    def issue_verified(self, slug: str, issue_id: str) -> bool:
        return self.verification(slug).get(issue_id, IssueVerification()).passes

    def mark_issue_passed(
        self, slug: str, issue_id: str, *, evidence: list[str], verified_by: str = "foreman"
    ) -> None:
        """Flip an issue to structurally-done. The ONLY path to ``passes:true``."""
        verification_json.set_passed(
            self.paths.verification_file(slug), issue_id,
            evidence=evidence, verified_at=self._clock(), verified_by=verified_by,
        )

    def mark_issue_failed(self, slug: str, issue_id: str) -> None:
        verification_json.set_failed(self.paths.verification_file(slug), issue_id)

    def seed_verification(self, slug: str) -> None:
        """Seed Default-FAIL entries for every current issue (idempotent)."""
        issues = self._load_issues(slug)
        verification_json.seed_missing(
            self.paths.verification_file(slug), [i.id for i in issues]
        )

    # ------------------------------------------------------------------ #
    # Gated documents (plan / adr / prd)
    # ------------------------------------------------------------------ #
    def _load_doc(self, slug: str, kind: str) -> Optional[GatedDoc]:
        path = self.paths.doc_file(slug, kind)
        if not path.exists():
            return None
        parsed = frontmatter.parse(path.read_text())
        version = _safe_int(parsed.get("version", 1), 1)
        status = _doc_status(parsed.get("status", DocStatus.DRAFTING.value))
        approval = Approval.from_dict(parsed.get("approval"))

        gd = GatedDoc(
            kind=kind, version=version, status=status, body=parsed.body, approval=approval,
        )

        # R3: auto-invalidate approval if the body changed since approval.
        if status == DocStatus.APPROVED:
            if not seal.intact(approval.body_sha256 if approval else None, parsed.body):
                gd.status = DocStatus.IN_REVIEW
                gd.approval = None
                # Persist the reverted status so disk is the source of truth.
                self._write_doc(slug, gd)
        return gd

    def write_doc(
        self,
        slug: str,
        kind: str,
        body: str,
        *,
        version: Optional[int] = None,
        status: DocStatus = DocStatus.IN_REVIEW,
    ) -> GatedDoc:
        """Write a new (or replacement) draft of a gated document."""
        if version is None:
            existing = self._load_doc(slug, kind)
            version = (existing.version + 1) if existing else 1
        gd = GatedDoc(kind=kind, version=version, status=status, body=body, approval=None)
        self._write_doc(slug, gd)
        return gd

    def _write_doc(self, slug: str, gd: GatedDoc) -> None:
        meta = {"kind": gd.kind, "version": gd.version, "status": gd.status.value}
        if gd.approval is not None:
            meta["approval"] = gd.approval.to_dict()
        self.paths.feature_dir(slug).mkdir(parents=True, exist_ok=True)
        self.paths.doc_file(slug, gd.kind).write_text(
            frontmatter.serialize(meta, gd.body)
        )

    def approve_doc(self, slug: str, kind: str, reviewer: str) -> GatedDoc:
        """Approve a document. Refuses if it still has open questions (§4.1/§12)."""
        gd = self._load_doc(slug, kind)
        if gd is None:
            raise FileNotFoundError(f"no {kind} document for feature {slug}")
        if gd.has_open_questions:
            raise ValueError(
                f"cannot approve {kind}: {len(gd.open_questions)} open question(s) remain"
            )
        gd.status = DocStatus.APPROVED
        gd.approval = Approval(
            reviewer=reviewer,
            timestamp=self._clock(),
            body_sha256=seal.fingerprint(gd.body),
        )
        self._write_doc(slug, gd)
        return gd

    def request_changes(self, slug: str, kind: str, reviewer: str, comments: str) -> Review:
        """Record a changes-requested review and flip the doc status."""
        gd = self._load_doc(slug, kind)
        if gd is None:
            raise FileNotFoundError(f"no {kind} document for feature {slug}")
        review = Review(
            doc_kind=kind,
            version=gd.version,
            action="request_changes",
            comments=comments,
            reviewer=reviewer,
            timestamp=self._clock(),
        )
        self._write_review(slug, review)
        gd.status = DocStatus.CHANGES_REQUESTED
        gd.approval = None
        self._write_doc(slug, gd)
        return review

    # ------------------------------------------------------------------ #
    # Reviews
    # ------------------------------------------------------------------ #
    def _write_review(self, slug: str, review: Review) -> None:
        self.paths.reviews_dir(slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "doc_kind": review.doc_kind,
            "version": review.version,
            "action": review.action,
            "reviewer": review.reviewer,
            "timestamp": review.timestamp,
        }
        self.paths.review_file(slug, review.doc_kind, review.version).write_text(
            frontmatter.serialize(meta, review.comments)
        )

    def latest_review(self, slug: str, kind: str, version: int) -> Optional[Review]:
        path = self.paths.review_file(slug, kind, version)
        if not path.exists():
            return None
        parsed = frontmatter.parse(path.read_text())
        return Review(
            doc_kind=kind,
            version=version,
            action=str(parsed.get("action", "request_changes")),
            comments=parsed.body,
            reviewer=str(parsed.get("reviewer", "")),
            timestamp=str(parsed.get("timestamp", "")),
        )

    def write_review_snapshot(self, slug: str, kind: str, version: int, body: str) -> None:
        """Snapshot the doc body a reviewer acted on, for diff-since-last-review (WS5.2)."""
        self.paths.reviews_dir(slug).mkdir(parents=True, exist_ok=True)
        (self.paths.reviews_dir(slug) / f"{kind}-v{version}-body.md").write_text(body)

    def read_review_snapshot(self, slug: str, kind: str, version: int) -> Optional[str]:
        """The snapshotted body for a reviewed version (None if absent)."""
        snap = self.paths.reviews_dir(slug) / f"{kind}-v{version}-body.md"
        return snap.read_text() if snap.exists() else None

    # ------------------------------------------------------------------ #
    # Issues
    # ------------------------------------------------------------------ #
    def _load_issues(self, slug: str) -> list[Issue]:
        idir = self.paths.issues_dir(slug)
        if not idir.exists():
            return []
        issues: list[Issue] = []
        for path in sorted(idir.glob("*.md")):
            parsed = frontmatter.parse(path.read_text())
            if not parsed.meta.get("id"):
                continue
            issues.append(self._issue_from_doc(parsed))
        issues.sort(key=lambda i: i.id)
        return issues

    @staticmethod
    def _issue_from_doc(parsed: frontmatter.Document) -> Issue:
        return Issue(
            id=str(parsed.get("id")),
            title=str(parsed.get("title", "")),
            status=_issue_status(parsed.get("status", IssueStatus.QUEUED.value)),
            depends_on=list(parsed.get("depends_on", []) or []),
            branch=str(parsed.get("branch", "")),
            attempts=int(parsed.get("attempts", 0)),
            budget=Budget.from_dict(parsed.get("budget")),
            prd_refs=list(parsed.get("prd_refs", []) or []),
            body=parsed.body,
            acceptance_check=str(parsed.get("acceptance_check", "") or ""),
            touches=list(parsed.get("touches", []) or []),
            kind=str(parsed.get("kind", ISSUE_KIND_FEATURE) or ISSUE_KIND_FEATURE),
        )

    def write_issue(self, slug: str, issue: Issue) -> None:
        self.paths.issues_dir(slug).mkdir(parents=True, exist_ok=True)
        self.paths.issue_file(slug, issue.id).write_text(
            frontmatter.serialize(issue.frontmatter(), issue.body)
        )

    def load_issue(self, slug: str, issue_id: str) -> Optional[Issue]:
        path = self.paths.issue_file(slug, issue_id)
        if not path.exists():
            return None
        return self._issue_from_doc(frontmatter.parse(path.read_text()))

    def update_issue_status(
        self, slug: str, issue_id: str, status: IssueStatus,
        *, attempts: Optional[int] = None, branch: Optional[str] = None,
    ) -> Issue:
        issue = self.load_issue(slug, issue_id)
        if issue is None:
            raise FileNotFoundError(f"no issue {issue_id} in feature {slug}")
        issue.status = status
        if attempts is not None:
            issue.attempts = attempts
        if branch is not None:
            issue.branch = branch
        self.write_issue(slug, issue)
        return issue

    def delete_issue(self, slug: str, issue_id: str) -> None:
        path = self.paths.issue_file(slug, issue_id)
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------ #
    # Queue confirmation gate (§6/§12 final gate)
    # ------------------------------------------------------------------ #
    def _queue_marker(self, slug: str) -> Path:
        return self.paths.feature_dir(slug) / ".queue_confirmed"

    def confirm_queue(self, slug: str) -> None:
        self._queue_marker(slug).write_text(self._clock() + "\n")

    def unconfirm_queue(self, slug: str) -> None:
        marker = self._queue_marker(slug)
        if marker.exists():
            marker.unlink()

    def _queue_confirmed(self, slug: str) -> bool:
        return self._queue_marker(slug).exists()

    # ------------------------------------------------------------------ #
    # Run records
    # ------------------------------------------------------------------ #
    def write_run_record(self, slug: str, record: RunRecord) -> None:
        rdir = self.paths.run_dir(slug, record.run_id)
        rdir.mkdir(parents=True, exist_ok=True)
        data = record.to_dict()
        # Flywheel-blindness fix: a run a richer terminal point never labels
        # (turn-extension intermediates, phase agents, bounced attempts) is stamped
        # with its terminal_reason here, so no terminal run is ever persisted blank.
        # A specific taxonomy stamp (success/escalated/evaluator_bounce) is written
        # with a non-blank outcome and so passes through unchanged.
        if not data.get("outcome"):
            from .retro import metrics as _metrics  # lazy: avoids an import cycle
            data["outcome"] = _metrics.terminal_outcome(data.get("terminal_reason"))
        self.paths.run_usage(slug, record.run_id).write_text(
            json.dumps(data, indent=2)
        )

    def write_run_summary(self, slug: str, run_id: str, summary: str) -> None:
        rdir = self.paths.run_dir(slug, run_id)
        rdir.mkdir(parents=True, exist_ok=True)
        self.paths.run_summary(slug, run_id).write_text(summary)

    def read_run_progress(self, slug: str, run_id: str) -> str:
        """The worker's progress.md handoff for a run (empty if not written)."""
        path = self.paths.run_progress(slug, run_id)
        return path.read_text() if path.exists() else ""

    def write_run_verdict(self, slug: str, run_id: str, payload: dict) -> None:
        """Persist the evaluator verdict JSON for a run (Foreman-owned, WS2.4)."""
        path = self.paths.run_verdict(slug, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")

    def write_run_audit(self, slug: str, run_id: str, payload: dict) -> None:
        """Persist the spec-auditor JSON for a run (WS5.1)."""
        path = self.paths.run_audit(slug, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")

    def audit_payloads(self, slug: str) -> list[dict]:
        """Raw audit.json payloads for a feature, newest run first (garbage skipped)."""
        rdir = self.paths.runs_dir(slug)
        out: list[dict] = []
        if not rdir.exists():
            return out
        for path in sorted(rdir.glob("*/audit.json"), reverse=True):
            try:
                out.append(json.loads(path.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
        return out

    def usage_records(self, slug: str) -> list[dict]:
        """Every run's usage.json payload for a feature, sorted by run dir (dicts only)."""
        rdir = self.paths.runs_dir(slug)
        out: list[dict] = []
        if not rdir.exists():
            return out
        for usage in sorted(rdir.glob("*/usage.json")):
            try:
                data = json.loads(usage.read_text())
            except (ValueError, OSError):
                continue
            if isinstance(data, dict):
                out.append(data)
        return out

    def write_report(self, slug: str, text: str) -> None:
        """Write the feature build report (report.md)."""
        self.paths.report_file(slug).write_text(text)

    # ------------------------------------------------------------------ #
    # Escalations (the attention queue's on-disk record)
    # ------------------------------------------------------------------ #
    def append_escalation(self, slug: str, issue_id: str, text: str) -> None:
        """Append to an issue's escalation file (created if absent)."""
        self.paths.escalations_dir(slug).mkdir(parents=True, exist_ok=True)
        path = self.paths.escalation_file(slug, issue_id)
        existing = path.read_text() if path.exists() else ""
        path.write_text(existing + text)

    def read_escalation(self, slug: str, issue_id: str) -> str:
        """An issue's escalation file text (empty if none)."""
        path = self.paths.escalation_file(slug, issue_id)
        return path.read_text() if path.exists() else ""

    # ------------------------------------------------------------------ #
    # Phase derivation — phase is always recomputed from disk state (R4)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _derive_phase(state: FeatureState) -> Phase:
        plan = state.doc("plan")
        adr = state.doc("adr")
        prd = state.doc("prd")

        def approved(d: Optional[GatedDoc]) -> bool:
            return d is not None and d.status == DocStatus.APPROVED

        docs_approved = approved(adr) and approved(prd)

        # Build / done states first.
        if docs_approved and state.queue_confirmed and state.issues:
            statuses = {i.status for i in state.issues}
            if statuses <= {IssueStatus.DONE, IssueStatus.MERGED}:
                return Phase.DONE
            return Phase.BUILDING
        if docs_approved and state.issues and not state.queue_confirmed:
            return Phase.QUEUE_REVIEW
        if docs_approved and not state.issues:
            return Phase.SLICING
        if approved(plan) and (adr is not None or prd is not None):
            return Phase.DOC_REVIEW
        if approved(plan):
            return Phase.GRILLING
        if plan is not None:
            return Phase.PLAN_REVIEW
        return Phase.REQUEST
