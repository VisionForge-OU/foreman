"""WS2 — evaluator verdict parsing, agent installer, PRD section extraction."""

import pytest

from foreman import prd
from foreman.agents import evaluator
from foreman.agents import installer as agents_installer


def _verdict_json(verdict="pass", scores=5, objections=None):
    import json
    obj = {
        "schema": "foreman-verdict/v1", "issue_id": "ISS-001", "verdict": verdict,
        "scores": {d: {"score": scores, "justification": "ok"}
                   for d in ("functionality", "prd_fidelity", "craft", "test_honesty")},
        "objections": objections or [], "summary": "s",
    }
    return "prose...\n```json\n" + json.dumps(obj) + "\n```\n"


def test_parse_pass_verdict():
    v = evaluator.parse(_verdict_json("pass", scores=5))
    assert v is not None and v.is_pass and not v.is_uncertain
    assert v.lowest == 5
    assert v.scores["functionality"].score == 5


def test_parse_objections_is_not_pass():
    v = evaluator.parse(_verdict_json("objections", scores=4,
                                      objections=["mocks the thing under test"]))
    assert not v.is_pass
    assert "mocks the thing under test" in v.feedback()


def test_pass_verdict_with_low_score_is_not_pass():
    # Says pass, but a rubric score is below the min threshold → not merge-worthy.
    v = evaluator.parse(_verdict_json("pass", scores=2), min_score=3)
    assert v.verdict == "pass"
    assert not v.is_pass


def test_pass_verdict_with_listed_objection_is_not_pass():
    v = evaluator.parse(_verdict_json("pass", scores=5, objections=["edge case X"]))
    assert not v.is_pass  # objections present override a 'pass'


def test_uncertain_verdict():
    v = evaluator.parse(_verdict_json("uncertain", scores=3))
    assert v.is_uncertain and not v.is_pass


def test_parse_unparseable_returns_none():
    assert evaluator.parse("no json here") is None
    assert evaluator.parse('```json\n{"schema":"other"}\n```') is None


# --- agent installer --- #

def test_packaged_agents_includes_evaluator():
    pkg = agents_installer.packaged_agents()
    assert pkg.get("foreman-evaluator") == 1


def test_install_and_status_and_missing(tmp_path):
    assert agents_installer.missing(tmp_path, ["foreman-evaluator"]) == ["foreman-evaluator"]
    written = agents_installer.install(tmp_path)
    assert "foreman-evaluator" in written
    states = {s.name: s.state for s in agents_installer.status(tmp_path)}
    assert states["foreman-evaluator"] == agents_installer.AgentState.OK
    assert agents_installer.missing(tmp_path, ["foreman-evaluator"]) == []
    # The installed agent is read-only (tools allowlist has no Write/Edit/Bash).
    text = (tmp_path / ".claude" / "agents" / "foreman-evaluator.md").read_text()
    assert "tools: Read, Grep, Glob" in text


# --- PRD section extraction (minimal context) --- #

PRD = """\
# PRD: thing

## Problem Statement
Users cannot do X.

## User Stories
1. As a user, I want X.
2. As a user, I want Y.

## User Flows
1. Do X: given A, when B, then C.

## Out of Scope
Z.
"""


def test_extract_named_section_only():
    out = prd.extract_sections(PRD, ["PRD §User Flows"])
    assert "Do X: given A" in out
    assert "Problem Statement" not in out  # only the referenced section


def test_extract_story_ref_pulls_user_stories():
    out = prd.extract_sections(PRD, ["Story #2"])
    assert "I want Y" in out and "User Stories" in out


def test_extract_no_match_returns_empty():
    assert prd.extract_sections(PRD, ["PRD §Nonexistent"]) == ""
