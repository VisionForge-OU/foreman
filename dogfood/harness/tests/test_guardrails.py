"""Unit tests for the campaign guardrails — the safety-critical auto-stop logic.

A bug here costs real money or runs unbounded unattended, so this is the one
module we drive test-first.
"""
from __future__ import annotations

import json
from pathlib import Path

from dogfood.harness.guardrails import Guardrails, foreman_spend


def test_status_ok_below_thresholds():
    g = Guardrails(cost_ceiling_usd=60.0, wall_clock_seconds=4 * 3600, warn_fraction=0.7)
    s = g.status(foreman_spend_usd=5.0, harness_spend_usd=1.0, elapsed_s=600)
    assert s.spent_usd == 6.0
    assert s.remaining_usd == 54.0
    assert s.level == "ok"
    assert not s.should_stop


def test_status_warns_at_70pct_of_cost():
    g = Guardrails(cost_ceiling_usd=60.0, wall_clock_seconds=4 * 3600, warn_fraction=0.7)
    s = g.status(foreman_spend_usd=42.0, harness_spend_usd=0.0, elapsed_s=10)
    assert s.level == "warn"
    assert not s.should_stop
    assert "cost" in s.reason.lower()


def test_status_warns_at_70pct_of_wallclock():
    g = Guardrails(cost_ceiling_usd=60.0, wall_clock_seconds=1000, warn_fraction=0.7)
    s = g.status(foreman_spend_usd=1.0, harness_spend_usd=0.0, elapsed_s=750)
    assert s.level == "warn"
    assert "wall" in s.reason.lower() or "time" in s.reason.lower()


def test_status_stops_at_cost_ceiling():
    g = Guardrails(cost_ceiling_usd=60.0, wall_clock_seconds=4 * 3600)
    s = g.status(foreman_spend_usd=59.5, harness_spend_usd=1.0, elapsed_s=10)
    assert s.level == "stop"
    assert s.should_stop


def test_status_stops_at_wallclock_ceiling():
    g = Guardrails(cost_ceiling_usd=60.0, wall_clock_seconds=1000)
    s = g.status(foreman_spend_usd=1.0, harness_spend_usd=0.0, elapsed_s=1000)
    assert s.should_stop


def test_can_afford_run_blocks_when_per_run_cap_would_exceed_ceiling():
    g = Guardrails(cost_ceiling_usd=60.0, wall_clock_seconds=4 * 3600,
                   per_run_max_cost_usd=1.50)
    # 59.0 spent + a 1.50 run could hit 60.5 > 60 → must not start it.
    assert not g.can_afford_run(spent_usd=59.0)
    assert g.can_afford_run(spent_usd=58.0)


def test_foreman_spend_sums_usage_json_across_features(tmp_path: Path):
    root = tmp_path / ".foreman" / "features"
    for slug, costs in {"f1": [0.10, 0.20], "f2": [0.30]}.items():
        for i, c in enumerate(costs):
            run = root / slug / "runs" / f"run-{i}"
            run.mkdir(parents=True)
            (run / "usage.json").write_text(json.dumps({"cost_usd": c, "label": slug}))
    assert abs(foreman_spend(tmp_path) - 0.60) < 1e-9


def test_foreman_spend_tolerates_missing_and_garbage(tmp_path: Path):
    root = tmp_path / ".foreman" / "features" / "f1" / "runs"
    (root / "good").mkdir(parents=True)
    (root / "good" / "usage.json").write_text(json.dumps({"cost_usd": 0.25}))
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "usage.json").write_text("not json{")
    (root / "nocost").mkdir(parents=True)
    (root / "nocost" / "usage.json").write_text(json.dumps({"label": "x"}))
    assert abs(foreman_spend(tmp_path) - 0.25) < 1e-9


def test_foreman_spend_zero_when_no_foreman_dir(tmp_path: Path):
    assert foreman_spend(tmp_path) == 0.0
