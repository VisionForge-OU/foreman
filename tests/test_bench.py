"""WS6 — foreman bench: eval-set round-trip, replay, deltas, cost ceiling."""

from __future__ import annotations

import json
import logging

import pytest

from foreman.retro import bench as B


# --------------------------------------------------------------------------- #
# Eval set round-trip
# --------------------------------------------------------------------------- #
def _cases(tmp_path):
    snap = tmp_path / "snapshots"
    snap.mkdir(exist_ok=True)
    return [
        B.BenchCase(name="add-done-cmd", repo_snapshot=snap / "todo",
                    issue={"id": "ISS-001", "title": "add done"},
                    expected_outcome="success_first_try"),
        B.BenchCase(name="flaky-retry", repo_snapshot=snap / "api",
                    issue={"id": "ISS-002", "title": "retry path"},
                    expected_outcome="success_after_retry(2)"),
    ]


def test_seed_and_load_round_trip(tmp_path):
    cases = _cases(tmp_path)
    eval_dir = tmp_path / "evalset"
    path = B.seed_eval_set(eval_dir, cases)
    assert path.exists()

    loaded = B.load_eval_set(eval_dir)
    assert len(loaded) == 2
    assert loaded[0].name == "add-done-cmd"
    assert loaded[0].expected_outcome == "success_first_try"
    assert loaded[1].issue["id"] == "ISS-002"


def test_load_eval_set_skips_blank_lines(tmp_path):
    eval_dir = tmp_path / "evalset"
    eval_dir.mkdir()
    (eval_dir / "cases.jsonl").write_text(
        json.dumps({"name": "c1", "expected_outcome": "success_first_try"})
        + "\n\n  \nnot json\n"
    )
    loaded = B.load_eval_set(eval_dir)
    assert len(loaded) == 1
    assert loaded[0].name == "c1"


def test_load_eval_set_missing_dir(tmp_path):
    assert B.load_eval_set(tmp_path / "nope") == []


# --------------------------------------------------------------------------- #
# run_bench with an injected fake runner
# --------------------------------------------------------------------------- #
async def test_run_bench_success_rate(tmp_path):
    cases = _cases(tmp_path)

    async def fake_runner(case):
        # First case matches expected, second does not.
        if case.name == "add-done-cmd":
            return {"outcome": "success_first_try", "cost_usd": 0.0, "turns": 3}
        return {"outcome": "escalated(budget)", "cost_usd": 0.0, "turns": 8}

    report = await B.run_bench(cases, runner_factory=fake_runner)
    assert len(report.results) == 2
    assert report.results[0].passed is True
    assert report.results[1].passed is False
    assert report.success_rate == pytest.approx(0.5)
    assert report.mean_turns == pytest.approx(5.5)


async def test_run_bench_after_retry_matches_by_stem(tmp_path):
    cases = _cases(tmp_path)

    async def fake_runner(case):
        if case.name == "flaky-retry":
            # actual retry count differs but stem matches -> passes
            return {"outcome": "success_after_retry(3)", "turns": 4}
        return {"outcome": "success_first_try", "turns": 2}

    report = await B.run_bench(cases, runner_factory=fake_runner)
    assert all(r.passed for r in report.results)
    assert report.success_rate == pytest.approx(1.0)


async def test_run_bench_accepts_benchresult(tmp_path):
    cases = _cases(tmp_path)[:1]

    async def fake_runner(case):
        return B.BenchResult(name=case.name, outcome="success_first_try",
                             cost_usd=0.1, turns=2)

    report = await B.run_bench(cases, runner_factory=fake_runner)
    assert report.results[0].passed


# --------------------------------------------------------------------------- #
# delta
# --------------------------------------------------------------------------- #
def test_delta_vs_baseline():
    baseline = B.BenchReport(success_rate=0.5, total_cost=2.0, mean_turns=6.0)
    current = B.BenchReport(success_rate=0.8, total_cost=1.5, mean_turns=4.0)
    d = current.delta(baseline)
    assert d["success_rate"] == pytest.approx(0.3)
    assert d["total_cost"] == pytest.approx(-0.5)
    assert d["mean_turns"] == pytest.approx(-2.0)


def test_render_report():
    report = B.BenchReport(
        results=[B.BenchResult(name="c1", outcome="success_first_try",
                               cost_usd=0.1, turns=3, passed=True)],
        success_rate=1.0, total_cost=0.1, mean_turns=3.0,
    )
    out = report.render()
    assert "success rate" in out and "c1" in out


# --------------------------------------------------------------------------- #
# cost ceiling — stops further cases and logs (no silent caps)
# --------------------------------------------------------------------------- #
async def test_cost_ceiling_stops_and_logs(tmp_path, caplog):
    snap = tmp_path / "s"
    snap.mkdir()
    cases = [
        B.BenchCase(name=f"c{i}", repo_snapshot=snap, issue={},
                    expected_outcome="success_first_try")
        for i in range(4)
    ]

    async def fake_runner(case):
        # each real run costs $1; ceiling is $2 -> after 2 runs, the rest skip
        return {"outcome": "success_first_try", "cost_usd": 1.0, "turns": 1}

    with caplog.at_level(logging.WARNING, logger="foreman.retro.bench"):
        report = await B.run_bench(
            cases, runner_factory=fake_runner, mocked=False, cost_ceiling_usd=2.0
        )

    assert len(report.results) == 2          # only 2 ran
    assert len(report.skipped) == 2          # 2 skipped
    assert "c2" in report.skipped and "c3" in report.skipped
    # the skip was logged, not silent
    assert any("cost ceiling" in r.message.lower() or "ceiling" in r.message.lower()
               for r in caplog.records)


async def test_mocked_mode_ignores_cost_for_ceiling(tmp_path):
    snap = tmp_path / "s"
    snap.mkdir()
    cases = [
        B.BenchCase(name=f"c{i}", repo_snapshot=snap, issue={},
                    expected_outcome="success_first_try")
        for i in range(3)
    ]

    async def fake_runner(case):
        return {"outcome": "success_first_try", "cost_usd": 99.0, "turns": 1}

    # mocked=True -> reported costs do not count against the ceiling, all run
    report = await B.run_bench(
        cases, runner_factory=fake_runner, mocked=True, cost_ceiling_usd=1.0
    )
    assert len(report.results) == 3
    assert report.skipped == []


# --------------------------------------------------------------------------- #
# attach_report
# --------------------------------------------------------------------------- #
def test_attach_report_writes_file(tmp_path):
    report = B.BenchReport(
        results=[B.BenchResult(name="c1", outcome="success_first_try", passed=True)],
        success_rate=1.0, total_cost=0.0, mean_turns=1.0,
    )
    proposal_dir = tmp_path / "proposals" / "p1"
    path = B.attach_report(proposal_dir, report)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["success_rate"] == 1.0
    assert data["results"][0]["name"] == "c1"
