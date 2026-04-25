"""IssueRun.run() drives a SINGLE issue directly — without the build() dispatch
loop, janitor cadence, e2e, or auditor. This is the isolation win of deepening 2:
one issue's lifecycle (lock → worker → gate → merge/bounce/escalate) is a unit.
"""

import pytest

from foreman.demo_scripts import demo_scripts
from foreman.issue_run import IssueRun
from foreman.models import IssueStatus

# Reuse the scheduler test scaffolding (sample repo + Phase-A via MockBackend).
from test_scheduler import _prepare_feature, _scheduler, _config


@pytest.mark.asyncio
async def test_single_issue_run_merges_and_flips_verification(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    sched = _scheduler(store, _config())
    await sched.worktrees.ensure_base()

    issue = store.load_issue(slug, "ISS-001")
    outcome = await IssueRun(sched, slug, issue).run()

    assert outcome == "done"
    # Foreman (never the worker) flipped the structural-done map.
    assert store.verification(slug)["ISS-001"].passes is True
    assert store.load_issue(slug, "ISS-001").status in (
        IssueStatus.MERGED, IssueStatus.DONE,
    )


@pytest.mark.asyncio
async def test_single_issue_run_escalates_when_retries_exhausted(tmp_path):
    repo, store, slug = await _prepare_feature(tmp_path)
    # First attempt writes a broken test → Foreman's gate fails it; with
    # max_retries=1 the first failure escalates instead of retrying.
    scripts = demo_scripts(fail_first_issue="ISS-001")
    sched = _scheduler(store, _config(max_retries=1), scripts=scripts)
    await sched.worktrees.ensure_base()

    issue = store.load_issue(slug, "ISS-001")
    outcome = await IssueRun(sched, slug, issue).run()

    assert outcome == "escalated"
    assert store.load_issue(slug, "ISS-001").status == IssueStatus.NEEDS_HUMAN
