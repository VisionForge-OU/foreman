import pytest

from foreman.demo import run_demo
from foreman.models import IssueStatus
from foreman.state import FileStore


@pytest.mark.asyncio
async def test_full_demo_pipeline(tmp_path):
    logs = []
    slug, report, repo = await run_demo(tmp_path, on_log=logs.append)

    store = FileStore(repo)
    state = store.load_feature(slug)

    # All issues landed.
    assert all(i.status in (IssueStatus.DONE, IssueStatus.MERGED) for i in state.issues)
    assert set(report.merged) == {"ISS-001", "ISS-002"}

    # The fail-first issue was retried (demonstrates Foreman catching a false claim).
    assert store.load_issue(slug, "ISS-001").attempts >= 1

    # E2E ran and passed.
    assert report.e2e == "passed"

    # Crash recovery: a brand-new store rebuilds the same terminal state from disk.
    from foreman.models import Phase
    assert FileStore(repo).load_feature(slug).phase == Phase.DONE

    # Report persisted.
    assert store.paths.report_file(slug).exists()


@pytest.mark.asyncio
async def test_demo_without_forced_failure(tmp_path):
    slug, report, repo = await run_demo(tmp_path, fail_first_issue=None, on_log=lambda m: None)
    assert set(report.merged) == {"ISS-001", "ISS-002"}
    assert report.escalated == []
