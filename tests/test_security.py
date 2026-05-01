"""WS7 — security-review gate agent verdict parsing + prompt."""

import json

from foreman.agents import security
from foreman.models import Issue


def _json(verdict="pass", findings=None):
    obj = {
        "schema": "foreman-security/v1", "issue_id": "ISS-001", "verdict": verdict,
        "findings": findings or [], "summary": "s",
    }
    return "prose...\n```json\n" + json.dumps(obj) + "\n```\n"


def _finding(sev):
    return {"severity": sev, "category": "command-injection", "file": "run.py",
            "line": 9, "description": "d", "recommendation": "r"}


def test_parse_pass_verdict():
    v = security.parse(_json("pass"))
    assert v is not None and v.is_pass and not v.is_uncertain


def test_parse_objections_is_not_pass():
    v = security.parse(_json("objections", findings=[_finding("high")]))
    assert not v.is_pass
    assert "command-injection" in v.feedback() and "run.py" in v.feedback()


def test_pass_with_high_finding_is_not_pass():
    # Says pass, but lists a high finding → not merge-worthy (guardrail).
    v = security.parse(_json("pass", findings=[_finding("medium")]))
    assert v.verdict == "pass"
    assert not v.is_pass


def test_pass_with_low_finding_still_passes():
    v = security.parse(_json("pass", findings=[_finding("low")]))
    assert v.is_pass


def test_uncertain_verdict():
    v = security.parse(_json("uncertain"))
    assert v.is_uncertain and not v.is_pass


def test_parse_unparseable_returns_none():
    assert security.parse("no json here") is None
    assert security.parse('```json\n{"schema":"other"}\n```') is None


def test_build_prompt_is_grounded_and_readonly():
    p = security.build_prompt(
        Issue(id="ISS-1", title="t", body="b"), prd_sections="PRDSEC",
        diff="THE DIFF", worktree="/wt",
    )
    assert "foreman-security/v1" in p
    assert "THE DIFF" in p
    assert "read-only" in p.lower()
