"""prompts.py — the single owner of continuation/extension + failure-report text."""

from foreman import prompts


def test_worker_continuation_mentions_resume_and_turns_and_ends_blank():
    out = prompts.worker_continuation(50)
    assert "RESUMED your prior session" in out
    assert "50 more turns" in out
    assert "do NOT restart from scratch" in out
    assert "FOREMAN-SUMMARY" in out
    assert out.endswith("\n\n")  # it is prepended to the worker prompt


def test_agent_continuation_shares_prefix_and_carries_task():
    out = prompts.agent_continuation("the audit and emit the required audit JSON block, then stop.")
    assert out.startswith(
        "CONTINUE — Foreman resumed your prior session with more turns. Finish "
    )
    assert out.endswith("the audit and emit the required audit JSON block, then stop.")


def test_pipeline_continuation_is_self_contained():
    out = prompts.pipeline_continuation()
    assert "RESUMED your prior" in out
    assert "output file(s)" in out


def test_with_failure_report_is_noop_when_empty():
    assert prompts.with_failure_report("base prompt", "") == "base prompt"


def test_with_failure_report_appends_distilled_block():
    out = prompts.with_failure_report("base prompt", "boom: 3 tests failed")
    assert out.startswith("base prompt")
    assert "--- PRIOR ATTEMPT FAILED — distilled report ---" in out
    assert out.endswith("boom: 3 tests failed")
