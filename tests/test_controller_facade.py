"""The TUI controller is a facade: screens cross ONE seam, not into core (deepening 6)."""

import pytest

from foreman import review
from foreman.tui.controller import Controller


def test_review_digest_delegates(tmp_path):
    c = Controller(tmp_path, demo=True)
    body = "# Doc\n\n## Decisions made on your behalf\n- chose X over Y\n\n## Body\nstuff\n"
    assert c.review_digest(body) == review.decisions_digest(body)


def test_kill_worker_returns_false_when_nothing_running(tmp_path):
    c = Controller(tmp_path, demo=True)
    assert c.kill_worker("ISS-999") is False


def test_config_path_points_at_config_yaml(tmp_path):
    c = Controller(tmp_path, demo=True)
    assert c.config_path().name == "config.yaml"


def test_escalation_text_roundtrip(tmp_path):
    c = Controller(tmp_path, demo=True)
    slug = c.create_feature("feat", "desc")
    assert c.escalation_text(slug, "ISS-001") == ""
    c.store.append_escalation(slug, "ISS-001", "boom\n")
    assert c.escalation_text(slug, "ISS-001") == "boom\n"
