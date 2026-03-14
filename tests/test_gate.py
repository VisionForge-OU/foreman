"""WS1 — the merge gate ties evidence + acceptance + suite + ratchet together."""

import sys

import pytest

from foreman import hooks
from foreman.models import Issue
from foreman.verification import gate, ratchet


def _worktree(tmp_path, *, failing=False):
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True)
    body = "def test_ok():\n    assert True\n"
    if failing:
        body += "\ndef test_regressed():\n    assert False\n"
    else:
        body += "\ndef test_regressed():\n    assert True\n"
    (wt / "tests" / "test_x.py").write_text(body)
    return wt


def _evidence(tmp_path):
    ed = tmp_path / "evidence"
    ed.mkdir()
    (ed / "test.log").write_text("2 passed")
    return ed


def _commands():
    return {"test": f"{sys.executable} -m pytest", "lint": "", "typecheck": ""}


async def test_gate_passes_clean(tmp_path):
    wt = _worktree(tmp_path)
    inst = hooks.install(wt, test_command=_commands()["test"], worker_id="ISS-001")
    issue = Issue(id="ISS-001", title="x",
                  acceptance_check=f"{sys.executable} -m pytest tests/test_x.py::test_ok")
    g = await gate.run_gate(
        worktree=wt, commands=_commands(), issue=issue, check_dir=None,
        evidence_dir=_evidence(tmp_path), baseline_path=tmp_path / "baseline.json",
        summary_evidence=["test.log"], env=inst.env,
    )
    assert g.passed, g.reason
    assert g.acceptance.passed
    # The structured trailer gave precise passing ids.
    assert g.now.has_passed_ids
    assert any("test_ok" in p for p in g.now.passed)
    hooks.cleanup(wt)


async def test_gate_blocks_on_missing_evidence(tmp_path):
    wt = _worktree(tmp_path)
    inst = hooks.install(wt, test_command=_commands()["test"], worker_id="ISS-001")
    empty_evidence = tmp_path / "evidence"
    empty_evidence.mkdir()
    issue = Issue(id="ISS-001", title="x", acceptance_check=f"{sys.executable} -m pytest")
    g = await gate.run_gate(
        worktree=wt, commands=_commands(), issue=issue, check_dir=None,
        evidence_dir=empty_evidence, baseline_path=tmp_path / "baseline.json",
        summary_evidence=[], env=inst.env,
    )
    assert not g.passed
    assert g.reason == "missing completion evidence"
    hooks.cleanup(wt)


async def test_gate_blocks_on_named_regression(tmp_path):
    wt = _worktree(tmp_path, failing=True)
    inst = hooks.install(wt, test_command=_commands()["test"], worker_id="ISS-001")
    # Baseline says tests/test_x.py::test_regressed was passing.
    baseline = tmp_path / "baseline.json"
    ratchet.write_baseline(baseline, {"tests/test_x.py::test_regressed"})
    issue = Issue(id="ISS-001", title="x",
                  acceptance_check=f"{sys.executable} -m pytest tests/test_x.py::test_ok")
    g = await gate.run_gate(
        worktree=wt, commands=_commands(), issue=issue, check_dir=None,
        evidence_dir=_evidence(tmp_path), baseline_path=baseline,
        summary_evidence=["test.log"], env=inst.env,
    )
    assert not g.passed
    assert "tests/test_x.py::test_regressed" in g.regressed
    assert "tests/test_x.py::test_regressed" in g.feedback
    hooks.cleanup(wt)
