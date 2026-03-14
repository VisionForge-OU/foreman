"""WS5 — spec-integrity audit: parse, report properties, amendment, fix issues."""

import json

from foreman import audit
from foreman.agents import installer as agents_installer


def _audit_json(requirements, summary="overall"):
    obj = {
        "schema": "foreman-audit/v1",
        "requirements": requirements,
        "summary": summary,
    }
    return "prose before...\n```json\n" + json.dumps(obj) + "\n```\ntrailing\n"


def _req(requirement, status="satisfied", evidence="", note=""):
    return {"requirement": requirement, "status": status,
            "evidence": evidence, "note": note}


# --- parsing --- #

def test_parse_all_satisfied():
    text = _audit_json([
        _req("R1", "satisfied", "src/a.py + tests/test_a.py"),
        _req("R2", "satisfied", "src/b.py"),
    ])
    rep = audit.parse(text)
    assert rep is not None
    assert rep.all_satisfied
    assert not rep.needs_amendment
    assert len(rep.satisfied) == 2
    assert rep.requirements[0].evidence == "src/a.py + tests/test_a.py"


def test_parse_diverged_needs_amendment():
    text = _audit_json([
        _req("R1", "satisfied", "src/a.py"),
        _req("expiry is 1h", "diverged", "src/x.py:expiry",
             note="Links actually expire after 24h."),
    ])
    rep = audit.parse(text)
    assert rep.needs_amendment
    assert not rep.all_satisfied
    assert len(rep.divergences) == 1
    assert rep.divergences[0].note == "Links actually expire after 24h."


def test_parse_unimplemented():
    text = _audit_json([
        _req("rate limit", "unimplemented", note="No throttle found."),
    ])
    rep = audit.parse(text)
    assert len(rep.unimplemented) == 1
    assert not rep.needs_amendment  # unimplemented alone doesn't force a spec amendment
    assert not rep.all_satisfied


def test_parse_unknown_status_degrades_to_unimplemented():
    text = _audit_json([_req("R1", "totally-bogus")])
    rep = audit.parse(text)
    assert rep.requirements[0].status == audit.UNIMPLEMENTED


def test_parse_picks_last_block():
    early = _audit_json([_req("old", "diverged", note="stale")])
    late = _audit_json([_req("new", "satisfied")])
    rep = audit.parse(early + "\n" + late)
    assert rep.all_satisfied
    assert rep.requirements[0].requirement == "new"


def test_parse_unparseable_returns_none():
    assert audit.parse("no json here") is None
    assert audit.parse('```json\n{"schema":"other"}\n```') is None
    assert audit.parse('```json\nnot-json\n```') is None
    assert audit.parse("") is None


def test_all_satisfied_false_when_empty():
    rep = audit.AuditReport(requirements=[])
    assert not rep.all_satisfied


# --- amendment building (deterministic) --- #

PRD = """\
# PRD: passwords

## Problem Statement
Users cannot reset their password.

## User Flows
1. Reset via email link.
"""


def test_build_amendment_appends_and_preserves_original():
    rep = audit.parse(_audit_json([
        _req("expiry is 1h", "diverged", "src/x.py:expiry",
             note="Links expire after 24h, not 1h."),
    ], summary="one divergence"))
    amended = audit.build_amendment(PRD, rep)
    # Original sections intact.
    assert "## Problem Statement" in amended
    assert "Reset via email link." in amended
    # Amendment section appended with the observed behaviour + rationale.
    assert audit.AMENDMENT_HEADING in amended
    assert "Links expire after 24h, not 1h." in amended
    assert "Rationale" in amended
    assert "one divergence" in amended
    # The original body must come before the amendment.
    assert amended.index("## Problem Statement") < amended.index(audit.AMENDMENT_HEADING)


def test_build_amendment_noop_without_divergence():
    rep = audit.parse(_audit_json([_req("R1", "satisfied")]))
    assert audit.build_amendment(PRD, rep) == PRD


def test_build_amendment_changes_body_hash():
    from foreman.hashing import body_hash
    rep = audit.parse(_audit_json([
        _req("R1", "diverged", note="different"),
    ]))
    amended = audit.build_amendment(PRD, rep)
    assert body_hash(amended) != body_hash(PRD)


# --- fix issue bodies --- #

def test_fix_issue_bodies_for_gaps():
    rep = audit.parse(_audit_json([
        _req("R1", "satisfied"),
        _req("rate limit", "unimplemented", note="missing"),
        _req("expiry", "diverged", "src/x.py", note="24h not 1h"),
    ]))
    issues = audit.fix_issue_bodies(rep)
    assert len(issues) == 2  # unimplemented + diverged, not satisfied
    titles = [i["title"] for i in issues]
    assert any("rate limit" in t for t in titles)
    assert any("expiry" in t for t in titles)
    for it in issues:
        assert "title" in it and "body" in it
        assert it["body"].strip()


# --- agent installer wires the auditor --- #

def test_packaged_agents_includes_auditor():
    pkg = agents_installer.packaged_agents()
    assert pkg.get("foreman-auditor") == 1


def test_install_auditor_is_read_only(tmp_path):
    agents_installer.install(tmp_path)
    text = (tmp_path / ".claude" / "agents" / "foreman-auditor.md").read_text()
    assert "tools: Read, Grep, Glob" in text
    assert "foreman-audit/v1" in text
