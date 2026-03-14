"""Filesystem layout for a target repo's ``.foreman/`` tree (§5).

Every path Foreman reads or writes inside a target repo is derived here, so the
on-disk contract lives in exactly one place.
"""

from __future__ import annotations

import re
from pathlib import Path


def slugify(text: str) -> str:
    """Turn a feature title into a filesystem-safe slug."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "feature"


class RepoPaths:
    """Path resolver rooted at a target repository."""

    def __init__(self, repo_root: Path | str):
        self.root = Path(repo_root).resolve()

    # --- top level ---
    @property
    def foreman_dir(self) -> Path:
        return self.root / ".foreman"

    @property
    def config_file(self) -> Path:
        return self.foreman_dir / "config.yaml"

    @property
    def features_dir(self) -> Path:
        return self.foreman_dir / "features"

    @property
    def skills_install_dir(self) -> Path:
        return self.root / ".claude" / "skills"

    @property
    def agents_install_dir(self) -> Path:
        """Where Foreman installs its read-only agent files (evaluator/auditor)."""
        return self.root / ".claude" / "agents"

    @property
    def daily_cost_file(self) -> Path:
        """Tracks accumulated spend per UTC day for the global ceiling (R5/§9)."""
        return self.foreman_dir / "daily_cost.json"

    @property
    def schema_version_file(self) -> Path:
        """On-disk schema version marker (P2.2). Absent ⇒ a Phase-1 (v1) tree."""
        return self.foreman_dir / "schema_version"

    @property
    def retro_dir(self) -> Path:
        """Where `foreman retro` drafts gated skill/rubric/prompt patch proposals (WS6.2)."""
        return self.foreman_dir / "retro"

    def retro_proposal_file(self, name: str) -> Path:
        return self.retro_dir / f"{name}.md"

    def retro_bench_file(self, name: str) -> Path:
        return self.retro_dir / f"{name}.bench.json"

    @property
    def skill_changelog_file(self) -> Path:
        return self.foreman_dir / "SKILL_CHANGELOG.md"

    def is_initialized(self) -> bool:
        return self.config_file.exists()

    def feature_slugs(self) -> list[str]:
        if not self.features_dir.exists():
            return []
        return sorted(p.name for p in self.features_dir.iterdir() if p.is_dir())

    # --- per feature ---
    def feature_dir(self, slug: str) -> Path:
        return self.features_dir / slug

    def request_file(self, slug: str) -> Path:
        return self.feature_dir(slug) / "request.md"

    def doc_file(self, slug: str, kind: str) -> Path:
        return self.feature_dir(slug) / f"{kind}.md"

    def reviews_dir(self, slug: str) -> Path:
        return self.feature_dir(slug) / "reviews"

    def review_file(self, slug: str, kind: str, version: int) -> Path:
        return self.reviews_dir(slug) / f"{kind}-v{version}-review.md"

    def issues_dir(self, slug: str) -> Path:
        return self.feature_dir(slug) / "issues"

    def issue_file(self, slug: str, issue_id: str) -> Path:
        return self.issues_dir(slug) / f"{issue_id}.md"

    def issue_check_dir(self, slug: str, issue_id: str) -> Path:
        """Holds an issue's runnable acceptance check (test file/script) — WS1.1."""
        return self.issues_dir(slug) / f"{issue_id}.check"

    # --- Phase-2 structural verification (P2.2) ---
    def verification_file(self, slug: str) -> Path:
        """Foreman-owned structural-done map. Workers are hook-blocked from writing it."""
        return self.feature_dir(slug) / "verification.json"

    def baseline_file(self, slug: str) -> Path:
        """Regression-ratchet baseline: the set of passing tests after each merge (WS1.4)."""
        return self.feature_dir(slug) / "baseline.json"

    def feature_state_file(self, slug: str) -> Path:
        """Initializer-seeded digest: status + conventions + gotchas (WS3.1)."""
        return self.feature_dir(slug) / "feature-state.md"

    def init_script(self, slug: str) -> Path:
        """Environment bootstrap any session runs first (WS3.1)."""
        return self.feature_dir(slug) / "init.sh"

    def escalations_dir(self, slug: str) -> Path:
        return self.feature_dir(slug) / "escalations"

    def escalation_file(self, slug: str, issue_id: str) -> Path:
        return self.escalations_dir(slug) / f"{issue_id}.md"

    def report_file(self, slug: str) -> Path:
        return self.feature_dir(slug) / "report.md"

    def runs_dir(self, slug: str) -> Path:
        return self.feature_dir(slug) / "runs"

    def run_dir(self, slug: str, run_id: str) -> Path:
        return self.runs_dir(slug) / run_id

    def run_transcript(self, slug: str, run_id: str) -> Path:
        return self.run_dir(slug, run_id) / "transcript.jsonl"

    def run_summary(self, slug: str, run_id: str) -> Path:
        return self.run_dir(slug, run_id) / "summary.md"

    def run_usage(self, slug: str, run_id: str) -> Path:
        return self.run_dir(slug, run_id) / "usage.json"

    def run_evidence_dir(self, slug: str, run_id: str) -> Path:
        """Where a worker saves evidence artifacts (logs/screenshots) — WS1.3."""
        return self.run_dir(slug, run_id) / "evidence"

    def run_progress(self, slug: str, run_id: str) -> Path:
        """Mandatory per-session handoff: what was done / remains / dead ends (WS3.2)."""
        return self.run_dir(slug, run_id) / "progress.md"

    def run_verdict(self, slug: str, run_id: str) -> Path:
        """Stored evaluator verdict for a run (WS2.4)."""
        return self.run_dir(slug, run_id) / "verdict.json"

    def run_audit(self, slug: str, run_id: str) -> Path:
        """Stored spec-integrity audit report for a run (WS5.1)."""
        return self.run_dir(slug, run_id) / "audit.json"
