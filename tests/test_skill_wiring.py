"""WS7 — the new worker skills are wired into their pipeline seams."""

from pathlib import Path

from foreman import config
from foreman.context.assembler import ContextAssembler
from foreman.models import Issue
from foreman.skill_invocation import SkillInvocation


def _issue():
    return Issue(id="ISS-001", title="t", body="b", acceptance_check="tests/x.py")


def test_planner_uses_foreman_plan_skill():
    out = SkillInvocation.planner("do x", "slug", Path("/p/plan.md"))
    assert "foreman-plan" in out


def test_e2e_uses_web_testing_skill_not_tdd():
    out = SkillInvocation.e2e("## User Flows\n1. x", "npx playwright test")
    assert "foreman-web-testing" in out
    assert "foreman-tdd" not in out


def test_worker_prompt_references_verify_skill():
    a = ContextAssembler()
    out = a.worker_prompt(_issue(), {"test": "pytest"}, evidence_dir=Path("/r/evidence"))
    assert "foreman-verify" in out.text


def test_worker_prompt_injects_debug_skill_only_on_retry():
    a = ContextAssembler()
    first = a.worker_prompt(_issue(), {"test": "pytest"})
    assert "foreman-debug" not in first.text
    retry = a.worker_prompt(_issue(), {"test": "pytest"}, failure_report="boom: a test failed")
    assert "foreman-debug" in retry.text


def test_stage_skills_in_required_defaults():
    assert "foreman-plan" in config.DEFAULT_REQUIRED_SKILLS
    assert "foreman-web-testing" in config.DEFAULT_REQUIRED_SKILLS
