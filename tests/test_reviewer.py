"""WS7 — code-review gate agent verdict parsing + prompt."""

import json

from foreman.agents import reviewer
from foreman.models import Issue


def _json(verdict="pass", issues=None, strengths=None):
    obj = {
        "schema": "foreman-codereview/v1", "issue_id": "ISS-001", "verdict": verdict,
        "strengths": strengths or [], "issues": issues or [], "summary": "s",
    }
    return "prose...\n```json\n" + json.dumps(obj) + "\n```\n"


def _issue(sev):
    return {"severity": sev, "file": "a.py", "line": 7, "what": "w", "why": "y", "fix": "f"}


def test_parse_pass_verdict():
    v = reviewer.parse(_json("pass"))
    assert v is not None and v.is_pass and not v.is_uncertain


def test_parse_objections_is_not_pass():
    v = reviewer.parse(_json("objections", issues=[_issue("critical")]))
    assert not v.is_pass
    assert "w" in v.feedback() and "a.py" in v.feedback()


def test_pass_with_blocking_issue_is_not_pass():
    # Says pass, but lists an important issue → not merge-worthy (guardrail).
    v = reviewer.parse(_json("pass", issues=[_issue("important")]))
    assert v.verdict == "pass"
    assert not v.is_pass


def test_pass_with_minor_issue_still_passes():
    # A 'pass' with only minor advisory notes stays merge-worthy.
    v = reviewer.parse(_json("pass", issues=[_issue("minor")], strengths=["clean seam"]))
    assert v.is_pass


def test_uncertain_verdict():
    v = reviewer.parse(_json("uncertain"))
    assert v.is_uncertain and not v.is_pass


def test_parse_unparseable_returns_none():
    assert reviewer.parse("no json here") is None
    assert reviewer.parse('```json\n{"schema":"other"}\n```') is None


def test_build_prompt_is_grounded_and_readonly():
    p = reviewer.build_prompt(
        Issue(id="ISS-1", title="t", body="b"), prd_sections="PRDSEC",
        diff="THE DIFF", worktree="/wt",
    )
    assert "foreman-codereview/v1" in p
    assert "THE DIFF" in p and "PRDSEC" in p
    assert "read-only" in p.lower()
