import pytest

from foreman.headless import run_feature, HeadlessError
from foreman.tui.controller import Controller


@pytest.mark.asyncio
async def test_headless_full_pipeline_mock(tmp_path):
    c = Controller(tmp_path, demo=True)
    logs = []
    slug, report = await run_feature(
        c, "Add done command", "mark todos done", auto_approve=True, on_log=logs.append,
    )
    assert set(report.merged) == {"ISS-001", "ISS-002"}
    assert report.e2e == "passed"
    # The grill open-questions loop was exercised and drained.
    joined = "\n".join(logs)
    assert "open question" in joined
    # Docs ended approved with zero open questions.
    st = c.feature(slug)
    assert not st.doc("prd").has_open_questions


@pytest.mark.asyncio
async def test_headless_requires_auto_approve(tmp_path):
    c = Controller(tmp_path, demo=True)
    with pytest.raises(HeadlessError):
        await run_feature(c, "x", "y", auto_approve=False, on_log=lambda m: None)
