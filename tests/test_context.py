"""WS3 — ContextAssembler, failure-distiller, feature initializer."""

from pathlib import Path

import pytest

from foreman.context import distiller, initializer
from foreman.context.assembler import ContextAssembler, estimate_tokens
from foreman.models import Issue
from foreman.summary import WorkerSummary


def _issue():
    return Issue(id="ISS-001", title="add done", body="## Goal\nmark done\n",
                 acceptance_check="tests/test_x.py", prd_refs=["PRD §User Flows"])


def test_assembler_includes_only_nonempty_sections_and_reports_breakdown():
    a = ContextAssembler()
    out = a.worker_prompt(
        _issue(), {"test": "pytest", "lint": "", "typecheck": ""},
        evidence_dir=Path("/tmp/runs/r1/evidence"),
        prd_sections="## User Flows\n1. mark done flow",
        feature_state="## Conventions\nsmall slices",
    )
    assert "ISS-001" in out.text
    assert "User Flows" in out.text
    assert "FEATURE STATE" in out.text
    # progress / failure_report / reviewer empty ⇒ omitted.
    assert "HANDOFF: progress.md" not in out.text
    assert "distilled failure report" not in out.text
    assert out.total_tokens > 0
    assert "issue" in out.breakdown and "prd" in out.breakdown


def test_assembler_truncates_over_budget_section():
    a = ContextAssembler(budgets={"prd": 10})  # 10 tokens ≈ 40 chars
    big = "x" * 5000
    out = a.worker_prompt(_issue(), {"test": "pytest"}, prd_sections=big)
    assert "truncated to fit context budget" in out.text
    assert out.breakdown["prd"] <= 40  # roughly the budget + marker


def test_assembler_names_progress_path_for_handoff():
    a = ContextAssembler()
    out = a.worker_prompt(_issue(), {"test": "pytest"},
                          evidence_dir=Path("/repo/.foreman/f/runs/r9/evidence"))
    assert "/repo/.foreman/f/runs/r9/progress.md" in out.text


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1


# --- distiller --- #

def test_distill_is_bounded_and_structured():
    summary = WorkerSummary(files_touched=["a.py", "b.py"], open_concerns=["maybe race"])
    report = distiller.distill(
        attempt=2, reason="acceptance check failed",
        failing_output="tests/test_x.py::test_a FAILED\nAssertionError", summary=summary,
    )
    assert "attempt #2" in report
    assert "What was attempted" in report and "a.py" in report
    assert "acceptance check failed" in report
    assert "test_a FAILED" in report
    assert "maybe race" in report
    assert len(report) <= distiller.MAX_CHARS + 60


def test_distill_truncates_huge_output():
    report = distiller.distill(attempt=1, reason="r", failing_output="z" * 9000)
    assert len(report) <= distiller.MAX_CHARS + 60
    assert "truncated" in report


# --- initializer --- #

def test_initializer_prompt_names_both_paths(tmp_path):
    p = initializer.build_prompt(
        slug="f", request="do x", commands={"test": "pytest"},
        init_path=tmp_path / "init.sh", feature_state_path=tmp_path / "feature-state.md",
    )
    assert str(tmp_path / "init.sh") in p
    assert str(tmp_path / "feature-state.md") in p
    assert "do x" in p


def test_initializer_fallback_writes_missing_artifacts(tmp_path):
    init_path = tmp_path / "init.sh"
    fs_path = tmp_path / "feature-state.md"
    flags = initializer.validate_and_fallback(
        slug="f", request="do x", commands={"test": "pytest -q"},
        init_path=init_path, feature_state_path=fs_path,
    )
    assert flags == {"init_sh": True, "feature_state": True}
    assert init_path.exists() and "bash" in init_path.read_text()
    assert "pytest -q" in fs_path.read_text()
    assert initializer.read_feature_state(fs_path).startswith("# Feature state")


def test_initializer_fallback_preserves_existing(tmp_path):
    init_path = tmp_path / "init.sh"
    fs_path = tmp_path / "feature-state.md"
    init_path.write_text("#!/bin/sh\necho mine\n")
    fs_path.write_text("# mine\n")
    flags = initializer.validate_and_fallback(
        slug="f", request="r", commands={}, init_path=init_path, feature_state_path=fs_path,
    )
    assert flags == {"init_sh": False, "feature_state": False}
    assert "echo mine" in init_path.read_text()
