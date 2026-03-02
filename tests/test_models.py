from foreman.models import (
    Budget,
    DocStatus,
    GatedDoc,
    FeatureState,
    Issue,
    IssueStatus,
)


def test_open_questions_extraction():
    body = (
        "## Summary\n\nstuff\n\n"
        "## Open questions for reviewer\n\n"
        "- Should we shard by tenant?\n"
        "- [x] Already answered: use UTC\n"
        "- ~~struck out~~\n"
        "- What is the retention window?\n\n"
        "## Next section\n\n- not a question\n"
    )
    gd = GatedDoc(kind="prd", version=1, status=DocStatus.IN_REVIEW, body=body)
    assert gd.open_questions == [
        "Should we shard by tenant?",
        "What is the retention window?",
    ]
    assert gd.has_open_questions


def test_no_open_questions():
    gd = GatedDoc(kind="prd", version=1, status=DocStatus.IN_REVIEW, body="## All done\n")
    assert gd.open_questions == []
    assert not gd.has_open_questions


def test_ready_issues_respects_dependencies():
    state = FeatureState(slug="x")
    state.issues = [
        Issue(id="ISS-001", title="a", status=IssueStatus.DONE),
        Issue(id="ISS-002", title="b", status=IssueStatus.QUEUED, depends_on=["ISS-001"]),
        Issue(id="ISS-003", title="c", status=IssueStatus.QUEUED, depends_on=["ISS-002"]),
        Issue(id="ISS-004", title="d", status=IssueStatus.QUEUED),
    ]
    ready = {i.id for i in state.ready_issues()}
    assert ready == {"ISS-002", "ISS-004"}  # 003 blocked by un-done 002


def test_budget_roundtrip():
    b = Budget(max_turns=10, max_cost_usd=1.5, timeout_min=5)
    assert Budget.from_dict(b.to_dict()) == b
