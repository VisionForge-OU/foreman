"""WS6 — outcome taxonomy + metrics aggregation."""

from __future__ import annotations

import itertools
import json

import pytest

from foreman.retro import metrics as M
from foreman.state import FileStore


# --------------------------------------------------------------------------- #
# Label helpers
# --------------------------------------------------------------------------- #
def test_label_success_boundaries():
    assert M.label_success(0) == "success_first_try"
    assert M.label_success(1) == "success_first_try"
    assert M.label_success(2) == "success_after_retry(2)"
    assert M.label_success(3) == "success_after_retry(3)"


def test_reason_labels():
    assert M.escalated("budget exceeded").startswith("escalated(")
    assert "budget exceeded" in M.escalated("budget exceeded")
    assert M.human_rejected("wrong approach") == "human_rejected(wrong approach)"
    assert M.evaluator_bounce() == "evaluator_bounce"


def test_reason_is_flattened():
    label = M.escalated("line one\nline two   with   spaces")
    assert "\n" not in label
    assert "  " not in M.label_param(label)


def test_base_label_and_param():
    assert M.base_label("success_after_retry(3)") == "success_after_retry"
    assert M.label_param("success_after_retry(3)") == "3"
    assert M.base_label("") == "legacy"
    assert M.base_label("escalated(budget)") == "escalated"
    assert M.label_param("escalated(budget)") == "budget"


def test_retries_of():
    assert M.retries_of("success_first_try") == 1
    assert M.retries_of("success_after_retry(4)") == 4
    assert M.retries_of("escalated(x)") == 0


def test_is_success():
    assert M.is_success("success_first_try")
    assert M.is_success("success_after_retry(2)")
    assert not M.is_success("evaluator_bounce")
    assert not M.is_success("legacy")


# --------------------------------------------------------------------------- #
# from_record
# --------------------------------------------------------------------------- #
def test_from_record_computes_wall_seconds_from_iso():
    m = M.from_record({
        "run_id": "r1", "label": "ISS-001", "outcome": "success_first_try",
        "started": "2026-01-01T00:00:00Z", "finished": "2026-01-01T00:01:30Z",
        "cost_usd": 0.5, "num_turns": 3, "prompt_tokens": 1200,
    })
    assert m.wall_seconds == 90.0
    assert m.issue_id == "ISS-001"
    assert m.is_success


def test_from_record_tolerant_of_missing_fields():
    m = M.from_record({})
    assert m.outcome == "legacy"
    assert m.cost_usd == 0.0
    assert m.wall_seconds == 0.0


def test_from_record_explicit_wall_seconds_wins():
    m = M.from_record({"label": "x", "wall_seconds": 12.5})
    assert m.wall_seconds == 12.5


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #
def _records():
    return [
        {"run_id": "r1", "label": "ISS-001", "issue_id": "ISS-001",
         "outcome": "success_first_try", "cost_usd": 1.0},
        {"run_id": "r2", "label": "ISS-002", "issue_id": "ISS-002",
         "outcome": "success_after_retry(3)", "cost_usd": 2.0},
        {"run_id": "r3", "label": "ISS-003", "issue_id": "ISS-003",
         "outcome": "escalated(budget exceeded)", "cost_usd": 1.0},
        {"run_id": "r4", "label": "ISS-004", "issue_id": "ISS-004",
         "outcome": "human_rejected(wrong approach)", "cost_usd": 0.0},
    ]


def test_aggregate_success_rate_and_retries():
    m = M.aggregate(_records())
    # 2 successes out of 4 distinct issues.
    assert m.success_rate == pytest.approx(0.5)
    # mean retries among successes: (1 + 3) / 2 = 2.0
    assert m.mean_retries == pytest.approx(2.0)


def test_aggregate_cost_per_issue():
    m = M.aggregate(_records())
    # total cost 4.0 over 4 distinct issues.
    assert m.total_cost == pytest.approx(4.0)
    assert m.cost_per_issue == pytest.approx(1.0)


def test_aggregate_escalation_histogram():
    m = M.aggregate(_records())
    assert m.escalation_reasons.get("budget exceeded") == 1
    assert m.escalation_reasons.get("wrong approach") == 1
    assert m.by_outcome.get("escalated") == 1
    assert m.by_outcome.get("human_rejected") == 1
    assert m.by_outcome.get("success_first_try") == 1


def test_aggregate_empty():
    m = M.aggregate([])
    assert m.n_runs == 0
    assert m.success_rate == 0.0
    assert m.cost_per_issue == 0.0


def test_aggregate_robust_to_missing_issue_id():
    # No issue_id, only labels — falls back to label-derived id / run_id.
    recs = [{"run_id": "r1", "label": "ISS-009", "outcome": "success_first_try"}]
    m = M.aggregate(recs)
    assert m.success_rate == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# load_feature_metrics
# --------------------------------------------------------------------------- #
def test_load_feature_metrics_over_tree(tmp_path):
    counter = itertools.count(1)
    store = FileStore(tmp_path, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    slug = store.create_feature("Metrics Feature", "desc")

    for rid, outcome, cost in [
        ("001-ISS-001", "success_first_try", 1.0),
        ("002-ISS-002", "success_after_retry(2)", 1.5),
        ("003-ISS-003", "escalated(timeout)", 0.5),
    ]:
        rdir = store.paths.run_dir(slug, rid)
        rdir.mkdir(parents=True, exist_ok=True)
        store.paths.run_usage(slug, rid).write_text(json.dumps({
            "run_id": rid, "label": rid.split("-", 1)[1],
            "issue_id": rid.split("-", 1)[1], "outcome": outcome, "cost_usd": cost,
        }))

    m = M.load_feature_metrics(store, slug)
    assert m.n_runs == 3
    assert m.success_rate == pytest.approx(2 / 3)
    assert m.escalation_reasons.get("timeout") == 1
    assert m.slug == slug


def test_load_feature_metrics_skips_garbage(tmp_path):
    store = FileStore(tmp_path)
    slug = store.create_feature("F", "d")
    rdir = store.paths.run_dir(slug, "bad")
    rdir.mkdir(parents=True, exist_ok=True)
    store.paths.run_usage(slug, "bad").write_text("not json {{{")
    m = M.load_feature_metrics(store, slug)
    assert m.n_runs == 0


def test_load_feature_metrics_no_runs(tmp_path):
    store = FileStore(tmp_path)
    slug = store.create_feature("F", "d")
    m = M.load_feature_metrics(store, slug)
    assert m.n_runs == 0


# --------------------------------------------------------------------------- #
# render / trend
# --------------------------------------------------------------------------- #
def test_render_produces_panel():
    m = M.aggregate(_records())
    out = M.render(m)
    assert "success rate" in out
    assert "cost / issue" in out
    assert "budget exceeded" in out


def test_trend_summary():
    a = M.aggregate(_records(), slug="feat-a")
    b = M.aggregate(_records(), slug="feat-b")
    out = M.trend([a, b])
    assert "feat-a" in out and "feat-b" in out
    assert "Δ" in out


def test_trend_empty():
    assert "no features" in M.trend([]).lower()
