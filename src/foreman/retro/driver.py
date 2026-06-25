"""Orchestration for `foreman retro` and `foreman bench` (WS6.2/6.3).

Keeps the agent spawn behind the backend seam so the whole flywheel is testable
with the MockBackend. A retro proposal is a single frontmatter ``.md`` file under
``.foreman/retro/`` that passes the **same hash-sealed human-review gate** as a PRD
(status drafting→in_review→approved, with a body-sha256 approval that
auto-invalidates if the body is edited). **No proposal can land without an
approved seal AND an attached bench report** (``retro.is_landable``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .. import frontmatter
from ..backend import AgentBackend, RunSpec
from .. import seal
from ..runner import AgentRunner
from . import bench as bench_mod
from . import metrics as metrics_mod
from . import retro as retro_mod

RETRO_AGENT = "foreman-retro"


def _records_for(store, slugs: list[str]) -> list[dict]:
    out: list[dict] = []
    for slug in slugs:
        out.extend(store.usage_records(slug))
    return out


async def analyze(
    store, config, backend: AgentBackend, *, slugs: list[str],
    runner: Optional[AgentRunner] = None,
    run_id_clock: Callable[[], str] = lambda: "retro",
) -> tuple[list[retro_mod.PatchProposal], list[retro_mod.FailureCluster], list]:
    """Cluster failures, spawn the read-only retro agent, return its proposals."""
    runner = runner or AgentRunner(backend)
    records = _records_for(store, slugs)
    clusters = retro_mod.cluster_failures(records)
    # Flywheel-blindness fix: a high kill rate (e.g. killed_turns) is a first-class
    # proposal trigger drafted deterministically, so the dominant failure is never
    # silently ignored even when the analysis agent proposes nothing.
    kill_proposals = retro_mod.propose_for_clusters(clusters, len(records))
    per_feature = [metrics_mod.load_feature_metrics(store, s) for s in slugs]
    digest = metrics_mod.trend(per_feature)
    prompt = retro_mod.build_analysis_prompt(clusters, digest)

    run_id = f"{run_id_clock()}-retro"
    spec = RunSpec(
        kind="retro", slug=slugs[0] if slugs else "_", repo_root=store.paths.root,
        cwd=store.paths.root, prompt=prompt, model=config.model_evaluator,
        effort=config.effort, permission_mode=config.permission_mode,
        budget=config.evaluator_budget, label="retro", agent=RETRO_AGENT,
    )
    result = await runner.run(spec, run_id=run_id)
    # Deterministic kill-rate proposals first (never lost), then the agent's — deduped
    # by (target, title) so an agent that also names a kill fix doesn't double it up.
    agent_proposals = retro_mod.parse_proposals(result.final_text)
    seen = {(p.target, p.title) for p in kill_proposals}
    proposals = kill_proposals + [
        p for p in agent_proposals if (p.target, p.title) not in seen
    ]
    return proposals, clusters, per_feature


# --------------------------------------------------------------------------- #
# Gated proposal store (one frontmatter .md per proposal in .foreman/retro/)
# --------------------------------------------------------------------------- #
@dataclass
class StoredProposal:
    name: str
    proposal: retro_mod.PatchProposal
    status: str           # drafting | in_review | approved
    sealed: bool          # approved AND body unchanged since approval


def draft(store, proposals: list[retro_mod.PatchProposal]) -> list[str]:
    """Write each proposal as a gated in_review doc. Returns the proposal names."""
    store.paths.retro_dir.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    existing = len(list(store.paths.retro_dir.glob("*.md")))
    for i, p in enumerate(proposals, start=existing + 1):
        name = f"p{i:03d}"
        meta = {
            "schema": "foreman-retro-proposal/v1",
            "name": name, "target": p.target, "title": p.title,
            "version_bump": p.version_bump, "status": "in_review",
            "diff": p.diff, "rationale": p.rationale,
        }
        body = retro_mod.proposal_to_review_doc(p)
        store.paths.retro_proposal_file(name).write_text(frontmatter.serialize(meta, body))
        names.append(name)
    return names


def load(store, name: str) -> Optional[StoredProposal]:
    path = store.paths.retro_proposal_file(name)
    if not path.exists():
        return None
    doc = frontmatter.parse(path.read_text())
    p = retro_mod.PatchProposal(
        target=str(doc.get("target", "")), title=str(doc.get("title", "")),
        rationale=str(doc.get("rationale", "")), diff=str(doc.get("diff", "")),
        version_bump=int(doc.get("version_bump", 1)),
    )
    status = str(doc.get("status", "in_review"))
    approval_hash = doc.get("body_sha256")
    sealed = status == "approved" and seal.intact(approval_hash, doc.body)
    if status == "approved" and not sealed:
        # Body changed after approval → auto-invalidate (R3), revert to in_review.
        status = "in_review"
        doc.meta["status"] = "in_review"
        doc.meta.pop("body_sha256", None)
        path.write_text(frontmatter.serialize(doc.meta, doc.body))
    return StoredProposal(name=name, proposal=p, status=status, sealed=sealed)


def approve(store, name: str, reviewer: str = "reviewer") -> StoredProposal:
    path = store.paths.retro_proposal_file(name)
    doc = frontmatter.parse(path.read_text())
    doc.meta["status"] = "approved"
    doc.meta["reviewer"] = reviewer
    doc.meta["body_sha256"] = seal.fingerprint(doc.body)
    path.write_text(frontmatter.serialize(doc.meta, doc.body))
    return load(store, name)


def reject(store, name: str, reviewer: str = "reviewer") -> Optional[StoredProposal]:
    """Reject a proposal: it can never land (kept for the audit trail, not deleted)."""
    path = store.paths.retro_proposal_file(name)
    if not path.exists():
        return None
    doc = frontmatter.parse(path.read_text())
    doc.meta["status"] = "rejected"
    doc.meta["reviewer"] = reviewer
    doc.meta.pop("body_sha256", None)
    path.write_text(frontmatter.serialize(doc.meta, doc.body))
    return load(store, name)


def attach_bench(store, name: str, report: bench_mod.BenchReport) -> Path:
    path = store.paths.retro_bench_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "success_rate": report.success_rate, "total_cost": report.total_cost,
        "mean_turns": report.mean_turns,
        "results": [r.__dict__ for r in report.results],
        "skipped": list(getattr(report, "skipped", []) or []),
    }, indent=2))
    return path


def _bench_report(store, name: str):
    path = store.paths.retro_bench_file(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def bench_report(store, name: str):
    """The attached bench report dict for a proposal (None if none attached)."""
    return _bench_report(store, name)


def list_names(store) -> list[str]:
    """All proposal names on disk, sorted (newest-numbered last)."""
    if not store.paths.retro_dir.exists():
        return []
    return sorted(p.stem for p in store.paths.retro_dir.glob("*.md"))


def land(store, name: str) -> str:
    """Apply an approved+benched proposal. Refuses otherwise (WS6.2/6.3 gate)."""
    sp = load(store, name)
    if sp is None:
        raise ValueError(f"no such proposal: {name}")
    if not sp.sealed:
        raise ValueError(f"{name} is not approved (status={sp.status}) — cannot land")
    report = _bench_report(store, name)
    if not retro_mod.is_landable(sp.proposal, report):
        raise ValueError(f"{name} has no bench report attached — no patch lands without one")
    if sp.proposal.is_skill:
        # Patch the skill installed in THIS target repo (never Foreman's packaged
        # distribution) — retro tunes the skills the target repo's workers use.
        skill_dir = store.paths.skills_install_dir / sp.proposal.skill_name
        new_version = retro_mod.apply_approved(sp.proposal, skill_dir)
        retro_mod.append_changelog(
            store.paths.skill_changelog_file, sp.proposal,
            approved_by=str(load(store, name).status), version=new_version,
        )
        return f"landed {name}: {sp.proposal.target} → v{new_version}"
    return f"{name} approved ({sp.proposal.target}); manual application required"
