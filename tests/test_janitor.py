"""WS4.3 — janitor issue factory + prompts."""

from pathlib import Path

from foreman import janitor
from foreman.models import ISSUE_KIND_JANITOR


def test_make_issue_is_janitor_kind_with_unknown_footprint():
    iss = janitor.make_issue("dedup", issue_id="JAN-001", branch="janitor/f/jan-001")
    assert iss.kind == ISSUE_KIND_JANITOR and iss.is_janitor
    assert iss.acceptance_check == ""          # janitors have no acceptance check
    assert iss.touches == [] and not iss.footprint_known  # runs alone
    assert "## Acceptance criteria" in iss.body


def test_all_kinds_have_prompts():
    for key in ("dedup", "conventions", "docs"):
        iss = janitor.make_issue(key, issue_id="JAN-001", branch="b")
        p = janitor.build_prompt(iss, key, evidence_dir=Path("/tmp/e"), feature_state="fs")
        assert key in p.lower() or janitor.KINDS[key].mandate[:20] in p
        assert "foreman-test" in p
        assert "/tmp/e" in p
        assert "fs" in p  # feature state included


def test_unknown_kind_raises():
    import pytest
    with pytest.raises(KeyError):
        janitor.make_issue("nope", issue_id="JAN-001", branch="b")
