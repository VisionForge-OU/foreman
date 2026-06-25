"""WS6 — foreman retro: clustering, proposals, the hash-sealed gate, apply."""

from __future__ import annotations

import json

import pytest

from foreman import frontmatter
from foreman.hashing import body_hash
from foreman.retro import retro as R
from foreman.retro.bench import BenchReport, BenchResult


# --------------------------------------------------------------------------- #
# cluster_failures
# --------------------------------------------------------------------------- #
def _records():
    return [
        {"run_id": "r1", "issue_id": "ISS-001", "outcome": "success_first_try"},
        {"run_id": "r2", "issue_id": "ISS-002", "outcome": "evaluator_bounce"},
        {"run_id": "r3", "issue_id": "ISS-003", "outcome": "evaluator_bounce"},
        {"run_id": "r4", "issue_id": "ISS-004", "outcome": "escalated(budget exceeded)"},
        {"run_id": "r5", "issue_id": "ISS-005", "outcome": "escalated(cost ceiling hit)"},
        {"run_id": "r6", "issue_id": "ISS-006", "outcome": "escalated(regression in tests)"},
        {"run_id": "r7", "issue_id": "ISS-007", "outcome": "legacy"},
    ]


def test_cluster_failures_groups_deterministically():
    clusters = R.cluster_failures(_records())
    patterns = {c.pattern: c.count for c in clusters}
    # successes/legacy excluded
    assert "evaluator_bounce" in patterns and patterns["evaluator_bounce"] == 2
    # budget + cost both fold into the budget bucket
    assert patterns.get("escalated:budget") == 2
    assert patterns.get("escalated:regression") == 1
    # sorted by descending count then pattern -> first is a 2-count cluster
    assert clusters[0].count == 2


def test_cluster_failures_examples_captured():
    clusters = R.cluster_failures(_records())
    bounce = next(c for c in clusters if c.pattern == "evaluator_bounce")
    assert "ISS-002" in bounce.examples and "ISS-003" in bounce.examples


def test_cluster_failures_empty():
    assert R.cluster_failures([]) == []
    assert R.cluster_failures([{"outcome": "success_first_try"}]) == []


def _kill_records():
    return [
        {"run_id": "k1", "issue_id": "ISS-001", "outcome": "killed_turns"},
        {"run_id": "k2", "issue_id": "ISS-002", "outcome": "killed_turns"},
        {"run_id": "k3", "issue_id": "ISS-003", "outcome": "killed_turns"},
        {"run_id": "k4", "issue_id": "ISS-004", "outcome": "killed_cost"},
        {"run_id": "c1", "label": "planner", "outcome": "completed"},      # not a failure
        {"run_id": "u1", "issue_id": "ISS-005", "outcome": "killed_user"}, # deliberate kill
        {"run_id": "s1", "issue_id": "ISS-006", "outcome": "success_first_try"},
    ]


def test_cluster_failures_surfaces_kill_reasons():
    """The dominant dogfood failure (killed_turns) must now form a cluster — the
    flywheel-blindness fix. completed/killed_user/success never cluster."""
    clusters = R.cluster_failures(_kill_records())
    patterns = {c.pattern: c.count for c in clusters}
    assert patterns.get("killed_turns") == 3
    assert patterns.get("killed_cost") == 1
    assert "completed" not in patterns
    assert "killed_user" not in patterns
    assert "success_first_try" not in patterns
    # sorted by descending count -> the turn-budget kill leads.
    assert clusters[0].pattern == "killed_turns"


# --------------------------------------------------------------------------- #
# propose_for_clusters — a high kill rate is a first-class proposal trigger (AC4)
# --------------------------------------------------------------------------- #
def test_high_kill_rate_drafts_a_proposal():
    clusters = R.cluster_failures(_kill_records())   # killed_turns = 3 of 7 runs (43%)
    proposals = R.propose_for_clusters(clusters, total_runs=7)
    assert proposals, "a dominant killed_turns cluster must draft a proposal"
    turn_props = [p for p in proposals if "turn" in p.title.lower()]
    assert turn_props, "the killed_turns cluster must propose the turn-budget fix"
    p = turn_props[0]
    assert "3" in p.rationale          # cites the cluster count
    assert p.diff.strip()              # concrete + reviewable
    assert R.PatchProposal is type(p)  # a real proposal that can go through the gate


def test_low_kill_rate_drafts_nothing():
    # One stray kill among many runs is below the rate threshold -> no proposal.
    clusters = R.cluster_failures(
        [{"run_id": "k1", "issue_id": "ISS-001", "outcome": "killed_turns"}]
        + [{"run_id": f"s{i}", "issue_id": f"ISS-{i:03d}",
            "outcome": "success_first_try"} for i in range(2, 21)]
    )
    assert R.propose_for_clusters(clusters, total_runs=20) == []


def test_propose_for_clusters_ignores_non_kill_clusters():
    # Escalations are the analysis agent's job, not the deterministic kill trigger.
    clusters = R.cluster_failures([
        {"run_id": "e1", "issue_id": "ISS-001", "outcome": "escalated(budget)"},
        {"run_id": "e2", "issue_id": "ISS-002", "outcome": "escalated(budget)"},
    ])
    assert R.propose_for_clusters(clusters, total_runs=2) == []


# --------------------------------------------------------------------------- #
# build_analysis_prompt
# --------------------------------------------------------------------------- #
def test_build_analysis_prompt_contains_clusters_and_schema():
    clusters = R.cluster_failures(_records())
    prompt = R.build_analysis_prompt(clusters, "3 issues, 2 escalations")
    assert R.PROPOSAL_SCHEMA in prompt
    assert "evaluator_bounce" in prompt
    assert "3 issues, 2 escalations" in prompt


# --------------------------------------------------------------------------- #
# parse_proposals
# --------------------------------------------------------------------------- #
def _proposal_block():
    return (
        "Here is my analysis.\n\n```json\n"
        + json.dumps({
            "schema": R.PROPOSAL_SCHEMA,
            "proposals": [
                {"target": "skill:foreman-tdd", "title": "Cap test re-runs",
                 "rationale": "evaluator_bounce cluster shows wasted turns",
                 "diff": "- old\n+ new", "version_bump": 1},
                {"target": "rubric", "title": "Add test-honesty objection",
                 "rationale": "graders missed fabricated passes",
                 "diff": "+ honesty check"},
            ],
        })
        + "\n```\n"
    )


def test_parse_proposals_parses_v1_block():
    props = R.parse_proposals(_proposal_block())
    assert len(props) == 2
    assert props[0].target == "skill:foreman-tdd"
    assert props[0].is_skill and props[0].skill_name == "foreman-tdd"
    assert props[0].version_bump == 1
    assert props[1].target == "rubric"
    assert props[1].version_bump == 1  # default


def test_parse_proposals_returns_empty_on_garbage():
    assert R.parse_proposals("no json here at all") == []
    assert R.parse_proposals("```json\nnot valid json {{{\n```") == []
    assert R.parse_proposals("") == []


def test_parse_proposals_skips_targetless_entries():
    text = "```json\n" + json.dumps({
        "schema": R.PROPOSAL_SCHEMA,
        "proposals": [{"title": "no target"}, {"target": "rubric", "title": "ok"}],
    }) + "\n```"
    props = R.parse_proposals(text)
    assert len(props) == 1
    assert props[0].target == "rubric"


# --------------------------------------------------------------------------- #
# proposal_to_review_doc + hash-sealed gate
# --------------------------------------------------------------------------- #
def test_proposal_to_review_doc_contains_diff_and_rationale():
    p = R.PatchProposal(
        target="skill:foreman-tdd", title="Cap re-runs",
        rationale="too many wasted turns", diff="- old\n+ new", version_bump=1,
    )
    doc = R.proposal_to_review_doc(p)
    assert "too many wasted turns" in doc
    assert "- old" in doc and "+ new" in doc
    assert "skill:foreman-tdd" in doc


def test_review_doc_goes_through_prd_gate(tmp_path):
    """A proposal review doc seals + auto-invalidates like a PRD (R3)."""
    import itertools
    from foreman.state import FileStore

    counter = itertools.count(1)
    store = FileStore(tmp_path, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    slug = store.create_feature("Retro", "desc")

    p = R.PatchProposal(target="rubric", title="x", rationale="y", diff="z")
    body = R.proposal_to_review_doc(p)
    store.write_doc(slug, "prd", body)  # same gate path as a PRD
    store.approve_doc(slug, "prd", reviewer="arash")
    assert store.load_feature(slug).doc("prd").status.value == "approved"

    # Tamper with the sealed body -> approval auto-invalidates.
    path = store.paths.doc_file(slug, "prd")
    path.write_text(path.read_text().replace("rubric", "rubric EDITED"))
    assert store.load_feature(slug).doc("prd").approval is None


# --------------------------------------------------------------------------- #
# append_changelog
# --------------------------------------------------------------------------- #
def test_append_changelog_writes_entry(tmp_path):
    p = R.PatchProposal(target="skill:foreman-tdd", title="Cap re-runs",
                        rationale="wasted turns", diff="d")
    path = tmp_path / "SKILL_CHANGELOG.md"
    R.append_changelog(path, p, approved_by="arash", version=3)
    text = path.read_text()
    assert "# SKILL_CHANGELOG" in text
    assert "skill:foreman-tdd → v3" in text
    assert "Cap re-runs" in text and "arash" in text

    # appending again does not duplicate the top header
    R.append_changelog(path, p, approved_by="arash", version=4)
    assert path.read_text().count("# SKILL_CHANGELOG") == 1


# --------------------------------------------------------------------------- #
# is_landable — the WS6 hard rule
# --------------------------------------------------------------------------- #
def test_is_landable_false_without_bench():
    p = R.PatchProposal(target="rubric", title="t", rationale="r", diff="d")
    assert R.is_landable(p, None) is False
    assert R.is_landable(p, "") is False
    assert R.is_landable(p, BenchReport(results=[])) is False
    assert R.is_landable(p, {"results": []}) is False


def test_is_landable_true_with_bench():
    p = R.PatchProposal(target="rubric", title="t", rationale="r", diff="d")
    report = BenchReport(results=[BenchResult(name="c1", outcome="success_first_try",
                                              passed=True)])
    assert R.is_landable(p, report) is True
    assert R.is_landable(p, {"results": [{"name": "c1"}]}) is True
    assert R.is_landable(p, "/path/to/bench_report.json") is True


# --------------------------------------------------------------------------- #
# apply_approved — version bump on a fixture skill
# --------------------------------------------------------------------------- #
def test_apply_approved_bumps_skill_version(tmp_path):
    skill_dir = tmp_path / "foreman-tdd"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        frontmatter.serialize(
            {"name": "foreman-tdd", "foreman_skill_version": 2}, "# Skill body\n"
        )
    )
    p = R.PatchProposal(target="skill:foreman-tdd", title="t", rationale="r",
                        diff="d", version_bump=1)
    new_version = R.apply_approved(p, skill_dir)
    assert new_version == 3
    doc = frontmatter.parse((skill_dir / "SKILL.md").read_text())
    assert int(doc.get("foreman_skill_version")) == 3
    assert "Skill body" in doc.body


def test_apply_approved_missing_skill_is_noop(tmp_path):
    p = R.PatchProposal(target="skill:ghost", title="t", rationale="r", diff="d")
    assert R.apply_approved(p, tmp_path / "ghost") == 0
