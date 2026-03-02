import itertools

import pytest

from foreman.models import Budget, DocStatus, Issue, IssueStatus, Phase
from foreman.state import FileStore


@pytest.fixture
def store(tmp_path):
    # Deterministic, monotonic clock for stable approval timestamps in tests.
    counter = itertools.count(1)
    return FileStore(tmp_path, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")


def test_create_and_recover_feature(store, tmp_path):
    slug = store.create_feature("Add Dark Mode", "Users want dark mode.")
    assert slug == "add-dark-mode"

    # Fresh store = pure disk recovery (R4).
    store2 = FileStore(tmp_path)
    state = store2.load_feature(slug)
    assert state.request == "Users want dark mode."
    assert state.phase == Phase.REQUEST


def test_doc_draft_review_approve_cycle(store):
    slug = store.create_feature("Feature X", "desc")
    store.write_doc(slug, "plan", "# Plan\n\nThe plan.")
    state = store.load_feature(slug)
    assert state.doc("plan").status == DocStatus.IN_REVIEW
    assert state.phase == Phase.PLAN_REVIEW

    store.approve_doc(slug, "plan", reviewer="arash")
    state = store.load_feature(slug)
    assert state.doc("plan").status == DocStatus.APPROVED
    assert state.doc("plan").approval.reviewer == "arash"
    assert state.phase == Phase.GRILLING


def test_approval_auto_invalidates_on_body_change(store):
    slug = store.create_feature("Feature X", "desc")
    store.write_doc(slug, "plan", "# Plan v1")
    store.approve_doc(slug, "plan", reviewer="arash")

    # Tamper with the body directly on disk, simulating an `e` edit (R3).
    path = store.paths.doc_file(slug, "plan")
    text = path.read_text().replace("# Plan v1", "# Plan v1 EDITED")
    path.write_text(text)

    state = store.load_feature(slug)
    assert state.doc("plan").status == DocStatus.IN_REVIEW
    assert state.doc("plan").approval is None


def test_cannot_approve_with_open_questions(store):
    slug = store.create_feature("Feature X", "desc")
    body = "## Open questions for reviewer\n\n- which db?\n"
    store.write_doc(slug, "prd", body)
    with pytest.raises(ValueError):
        store.approve_doc(slug, "prd", reviewer="arash")


def test_request_changes_records_review_and_status(store):
    slug = store.create_feature("Feature X", "desc")
    store.write_doc(slug, "plan", "# Plan v1")
    review = store.request_changes(slug, "plan", reviewer="arash", comments="too vague")
    assert review.version == 1
    state = store.load_feature(slug)
    assert state.doc("plan").status == DocStatus.CHANGES_REQUESTED
    fetched = store.latest_review(slug, "plan", 1)
    assert fetched.comments.strip() == "too vague"


def test_write_doc_increments_version(store):
    slug = store.create_feature("Feature X", "desc")
    v1 = store.write_doc(slug, "plan", "v1")
    v2 = store.write_doc(slug, "plan", "v2")
    assert (v1.version, v2.version) == (1, 2)


def test_issue_persistence_and_status_update(store):
    slug = store.create_feature("Feature X", "desc")
    issue = Issue(
        id="ISS-001", title="thin slice", depends_on=[],
        branch="feature/x/iss-001", budget=Budget(max_turns=5),
        prd_refs=["PRD §3.2"], body="## Goal\nship it",
    )
    store.write_issue(slug, issue)
    loaded = store.load_issue(slug, "ISS-001")
    assert loaded.title == "thin slice"
    assert loaded.prd_refs == ["PRD §3.2"]
    assert loaded.budget.max_turns == 5

    store.update_issue_status(slug, "ISS-001", IssueStatus.DONE, attempts=2)
    assert store.load_issue(slug, "ISS-001").status == IssueStatus.DONE
    assert store.load_issue(slug, "ISS-001").attempts == 2


def test_phase_progression_to_building_requires_queue_confirmation(store):
    slug = store.create_feature("Feature X", "desc")
    store.write_doc(slug, "plan", "p")
    store.approve_doc(slug, "plan", "arash")
    store.write_doc(slug, "prd", "prd body")
    store.approve_doc(slug, "prd", "arash")
    store.write_issue(slug, Issue(id="ISS-001", title="a"))

    # PRD approved + issues exist but queue NOT confirmed -> QUEUE_REVIEW (the gate).
    assert store.load_feature(slug).phase == Phase.QUEUE_REVIEW

    store.confirm_queue(slug)
    assert store.load_feature(slug).phase == Phase.BUILDING


def test_done_phase_when_all_issues_done(store):
    slug = store.create_feature("Feature X", "desc")
    store.write_doc(slug, "prd", "prd")
    store.approve_doc(slug, "prd", "arash")
    store.write_issue(slug, Issue(id="ISS-001", title="a", status=IssueStatus.MERGED))
    store.confirm_queue(slug)
    assert store.load_feature(slug).phase == Phase.DONE
