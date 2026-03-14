"""WS0 — schema v2: new issue fields, verification.json, and additive migration."""

import itertools
import json

import pytest

from foreman.models import (
    Issue,
    IssueStatus,
    IssueVerification,
    SCHEMA_VERSION,
)
from foreman.state import FileStore
from foreman.verification import verification_json


@pytest.fixture
def store(tmp_path):
    counter = itertools.count(1)
    return FileStore(tmp_path, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")


def test_new_issue_v2_fields_round_trip(store):
    slug = store.create_feature("F", "d")
    issue = Issue(
        id="ISS-001", title="slice",
        acceptance_check="issues/ISS-001.check/test_it.py",
        touches=["src/a.py", "src/b.py"], kind="janitor",
        status=IssueStatus.AWAITING_EVALUATION,
    )
    store.write_issue(slug, issue)
    loaded = store.load_issue(slug, "ISS-001")
    assert loaded.acceptance_check == "issues/ISS-001.check/test_it.py"
    assert loaded.touches == ["src/a.py", "src/b.py"]
    assert loaded.kind == "janitor"
    assert loaded.is_janitor
    assert loaded.status == IssueStatus.AWAITING_EVALUATION


def test_issue_footprint_known(store):
    assert not Issue(id="ISS-001", title="x").footprint_known
    assert Issue(id="ISS-002", title="x", touches=["a"]).footprint_known


def test_unknown_issue_status_degrades_to_queued(store, tmp_path):
    slug = store.create_feature("F", "d")
    store.write_issue(slug, Issue(id="ISS-001", title="x"))
    # Forge a status a newer Foreman might write.
    p = store.paths.issue_file(slug, "ISS-001")
    p.write_text(p.read_text().replace("status: queued", "status: time_traveling"))
    assert store.load_issue(slug, "ISS-001").status == IssueStatus.QUEUED


def test_new_tree_is_stamped_v2(store):
    store.create_feature("F", "d")
    assert store.schema_version() == SCHEMA_VERSION
    assert store.paths.schema_version_file.exists()


# --- verification.json (Default-FAIL contract) --- #

def test_verification_default_fail_and_flip(store):
    slug = store.create_feature("F", "d")
    store.write_issue(slug, Issue(id="ISS-001", title="x"))
    store.seed_verification(slug)
    assert store.issue_verified(slug, "ISS-001") is False

    store.mark_issue_passed(slug, "ISS-001", evidence=["runs/r1/evidence/test.log"])
    v = store.verification(slug)["ISS-001"]
    assert v.passes is True
    assert v.evidence == ["runs/r1/evidence/test.log"]
    assert v.verified_by == "foreman"
    assert v.verified_at  # timestamped by the store clock

    store.mark_issue_failed(slug, "ISS-001")
    assert store.issue_verified(slug, "ISS-001") is False


def test_verification_json_tolerant_of_corruption(store, tmp_path):
    slug = store.create_feature("F", "d")
    store.paths.verification_file(slug).write_text("{not json")
    assert store.verification(slug) == {}


# --- additive v1 -> v2 migration --- #

def _build_v1_tree(tmp_path):
    """A Phase-1 tree: features + issues but NO schema_version marker, NO
    verification.json. We write the issue files by hand to mimic v1 on disk."""
    store = FileStore(tmp_path, clock=lambda: "2026-01-01T00:00:00Z")
    slug = store.create_feature("Legacy Feature", "request")
    # create_feature stamps v2 — remove the marker to simulate a real v1 tree.
    store.paths.schema_version_file.unlink()
    # Write v1-style issue files (no acceptance_check/touches/kind fields).
    idir = store.paths.issues_dir(slug)
    (idir / "ISS-001.md").write_text(
        "---\nid: ISS-001\ntitle: merged one\nstatus: merged\n"
        "depends_on: []\nbranch: b\nattempts: 1\n"
        "budget: {max_turns: 80, max_cost_usd: 5.0, timeout_min: 45}\n"
        "prd_refs: ['PRD §1']\n---\n## Goal\ndone already\n"
    )
    (idir / "ISS-002.md").write_text(
        "---\nid: ISS-002\ntitle: queued one\nstatus: queued\n"
        "depends_on: []\nbranch: b2\nattempts: 0\n"
        "budget: {max_turns: 80, max_cost_usd: 5.0, timeout_min: 45}\n"
        "prd_refs: ['PRD §2']\n---\n## Goal\ntodo\n"
    )
    return slug


def test_v1_tree_migrates_additively(tmp_path):
    slug = _build_v1_tree(tmp_path)
    iss1_before = (FileStore(tmp_path).paths.issue_file(slug, "ISS-001")).read_text()

    store = FileStore(tmp_path)
    assert store.schema_version() == 1  # detected as v1

    state = store.load_feature(slug)  # triggers migration

    # Version stamped, verification.json seeded.
    assert store.schema_version() == SCHEMA_VERSION
    v = store.verification(slug)
    assert v["ISS-001"].passes is True   # merged issue → baseline pass
    assert v["ISS-001"].verified_by == "migration"
    assert v["ISS-002"].passes is False  # queued → Default-FAIL

    # Phase-1 issue files are byte-for-byte untouched (non-destructive).
    iss1_after = store.paths.issue_file(slug, "ISS-001").read_text()
    assert iss1_after == iss1_before

    # Old issues still load with v2 defaults.
    iss2 = state.issue("ISS-002")
    assert iss2.kind == "feature"
    assert iss2.touches == []
    assert not iss2.footprint_known


def test_migration_is_idempotent(tmp_path):
    slug = _build_v1_tree(tmp_path)
    store = FileStore(tmp_path)
    store.load_feature(slug)
    store.mark_issue_passed(slug, "ISS-002", evidence=["e"])  # human/Foreman progress

    # A fresh store re-running migration must NOT clobber existing entries.
    store2 = FileStore(tmp_path)
    store2.load_feature(slug)
    assert store2.issue_verified(slug, "ISS-002") is True
