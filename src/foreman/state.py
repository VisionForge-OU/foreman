"""FileStore — the only state of record (R4).

All durable Foreman state is human-readable files under a target repo's
``.foreman/`` tree. This module reads and writes them and rebuilds the in-memory
:class:`FeatureState` from disk, so killing Foreman and restarting fully recovers
state. It also enforces the approval-invalidation rule (R3): if a gated document's
body changed since it was approved, the approval is dropped and status reverts to
``in_review`` *at load time* — making approval a pure function of file contents.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import frontmatter
from .hashing import body_hash
from .models import (
    Approval,
    Budget,
    DocStatus,
    GatedDoc,
    FeatureState,
    Issue,
    IssueStatus,
    Phase,
    Review,
    RunRecord,
    DOC_KINDS,
)
from .paths import RepoPaths, slugify


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FileStore:
    """Reads/writes the ``.foreman/`` tree for a single target repo."""

    def __init__(self, repo_root: Path | str, clock: Callable[[], str] = _utcnow_iso):
        self.paths = RepoPaths(repo_root)
        self._clock = clock

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
        return slug

    def list_features(self) -> list[str]:
        return self.paths.feature_slugs()

    def load_feature(self, slug: str) -> FeatureState:
        """Rebuild a feature's full state from disk (R4)."""
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
        state.phase = self._derive_phase(state)
        return state

    # ------------------------------------------------------------------ #
    # Gated documents (plan / adr / prd)
    # ------------------------------------------------------------------ #
    def _load_doc(self, slug: str, kind: str) -> Optional[GatedDoc]:
        path = self.paths.doc_file(slug, kind)
        if not path.exists():
            return None
        parsed = frontmatter.parse(path.read_text())
        version = int(parsed.get("version", 1))
        status = DocStatus(parsed.get("status", DocStatus.DRAFTING.value))
        approval = Approval.from_dict(parsed.get("approval"))

        gd = GatedDoc(
            kind=kind, version=version, status=status, body=parsed.body, approval=approval,
        )

        # R3: auto-invalidate approval if the body changed since approval.
        if status == DocStatus.APPROVED:
            if approval is None or approval.body_sha256 != body_hash(parsed.body):
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
            body_sha256=body_hash(gd.body),
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
            status=IssueStatus(parsed.get("status", IssueStatus.QUEUED.value)),
            depends_on=list(parsed.get("depends_on", []) or []),
            branch=str(parsed.get("branch", "")),
            attempts=int(parsed.get("attempts", 0)),
            budget=Budget.from_dict(parsed.get("budget")),
            prd_refs=list(parsed.get("prd_refs", []) or []),
            body=parsed.body,
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
        import json

        rdir = self.paths.run_dir(slug, record.run_id)
        rdir.mkdir(parents=True, exist_ok=True)
        self.paths.run_usage(slug, record.run_id).write_text(
            json.dumps(record.to_dict(), indent=2)
        )

    def write_run_summary(self, slug: str, run_id: str, summary: str) -> None:
        rdir = self.paths.run_dir(slug, run_id)
        rdir.mkdir(parents=True, exist_ok=True)
        self.paths.run_summary(slug, run_id).write_text(summary)

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

        # Build / done states first.
        if approved(prd) and state.queue_confirmed and state.issues:
            statuses = {i.status for i in state.issues}
            if statuses <= {IssueStatus.DONE, IssueStatus.MERGED}:
                return Phase.DONE
            return Phase.BUILDING
        if approved(prd) and state.issues and not state.queue_confirmed:
            return Phase.QUEUE_REVIEW
        if approved(prd) and not state.issues:
            return Phase.SLICING
        if approved(plan) and (adr is not None or prd is not None):
            return Phase.DOC_REVIEW
        if approved(plan):
            return Phase.GRILLING
        if plan is not None:
            return Phase.PLAN_REVIEW
        return Phase.REQUEST
