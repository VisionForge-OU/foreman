"""WS1 — acceptance checks, evidence contract, regression ratchet."""

import pytest

from foreman.models import Issue
from foreman.verification import checks, evidence, ratchet


# --- WS1.1 acceptance checks --- #

def test_issues_missing_checks_flags_only_featureless_checks():
    issues = [
        Issue(id="ISS-001", title="ok", acceptance_check="pytest tests/x.py::t"),
        Issue(id="ISS-002", title="missing"),
        Issue(id="ISS-003", title="janitor-no-check", kind="janitor"),
    ]
    assert checks.issues_missing_checks(issues) == ["ISS-002"]


def test_acceptance_check_command_vs_test_file():
    cmd = checks.AcceptanceCheck(spec="pytest tests/x.py::test_a")
    assert cmd.present and cmd.is_command
    assert cmd.command({"test": "pytest"}) == "pytest tests/x.py::test_a"

    path = checks.AcceptanceCheck(spec="tests/test_a.py")
    assert path.present and not path.is_command
    assert path.command({"test": "pytest -q"}) == "pytest -q tests/test_a.py"

    absent = checks.AcceptanceCheck(spec="")
    assert not absent.present
    assert absent.command({"test": "pytest"}) is None


def test_acceptance_check_installs_canonical_artifacts(tmp_path):
    check_dir = tmp_path / "ISS-001.check"
    (check_dir / "tests").mkdir(parents=True)
    (check_dir / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()

    ac = checks.AcceptanceCheck(spec="tests/test_x.py", check_dir=check_dir)
    installed = ac.install_into(worktree)
    assert installed == ["tests/test_x.py"]
    assert (worktree / "tests" / "test_x.py").exists()


async def test_acceptance_check_run_executes_command(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    ac = checks.AcceptanceCheck(spec="test 1 = 1")  # multi-token ⇒ command; exits 0
    outcome = await ac.run(worktree, {"test": "pytest"})
    assert outcome.ran and outcome.passed

    ac_fail = checks.AcceptanceCheck(spec="test 1 = 2")  # exits 1
    outcome = await ac_fail.run(worktree, {"test": "pytest"})
    assert outcome.ran and not outcome.passed


# --- WS1.3 evidence contract --- #

def test_evidence_rejects_empty_dir(tmp_path):
    res = evidence.validate(tmp_path / "evidence", claimed=["test.log"])
    assert not res.ok
    assert "no non-empty evidence" in res.reason


def test_evidence_rejects_empty_file(tmp_path):
    ed = tmp_path / "evidence"
    ed.mkdir()
    (ed / "test.log").write_text("")  # zero bytes
    res = evidence.validate(ed, claimed=["test.log"])
    assert not res.ok


def test_evidence_accepts_nonempty_and_matches_claim(tmp_path):
    ed = tmp_path / "evidence"
    ed.mkdir()
    (ed / "test.log").write_text("3 passed")
    res = evidence.validate(ed, claimed=["runs/r1/evidence/test.log"])  # path by basename
    assert res.ok
    assert res.artifacts == ["test.log"]


def test_evidence_rejects_when_claimed_artifact_absent(tmp_path):
    ed = tmp_path / "evidence"
    ed.mkdir()
    (ed / "present.log").write_text("ok")
    res = evidence.validate(ed, claimed=["present.log", "screenshot.png"])
    assert not res.ok
    assert res.missing == ["screenshot.png"]


# --- WS1.4 regression ratchet --- #

def test_parse_pytest_verbose():
    out = (
        "tests/test_a.py::test_one PASSED\n"
        "tests/test_a.py::test_two FAILED\n"
        "tests/test_b.py::test_three PASSED\n"
    )
    r = ratchet.parse_test_output(out)
    assert r.has_passed_ids
    assert r.passed == {"tests/test_a.py::test_one", "tests/test_b.py::test_three"}
    assert r.failed == {"tests/test_a.py::test_two"}


def test_parse_foreman_test_trailer_is_authoritative():
    out = 'noise\nFOREMAN-TEST-RESULTS {"passed": ["a::t1"], "failed": ["a::t2"]}\n'
    r = ratchet.parse_test_output(out)
    assert r.passed == {"a::t1"} and r.failed == {"a::t2"} and r.has_passed_ids


def test_parse_pytest_quiet_failures_only():
    out = "FAILED tests/test_a.py::test_two - AssertionError\n1 failed, 2 passed\n"
    r = ratchet.parse_test_output(out)
    assert r.failed == {"tests/test_a.py::test_two"}
    assert not r.has_passed_ids  # passed ids not enumerable from quiet output


def test_ratchet_detects_named_regression(tmp_path):
    bpath = tmp_path / "baseline.json"
    ratchet.write_baseline(bpath, {"a::t1", "a::t2"})
    now = ratchet.TestResults(passed={"a::t2"}, failed={"a::t1"}, has_passed_ids=True)
    res = ratchet.check(ratchet.read_baseline(bpath), now)
    assert not res.ok
    assert res.regressed == ["a::t1"]
    assert "a::t1" in res.report()


def test_ratchet_passes_when_no_baseline_regression(tmp_path):
    bpath = tmp_path / "baseline.json"
    ratchet.write_baseline(bpath, {"a::t1"})
    now = ratchet.TestResults(passed={"a::t1", "a::t2"}, failed=set(), has_passed_ids=True)
    assert ratchet.check(ratchet.read_baseline(bpath), now).ok
    updated = ratchet.update_baseline(bpath, now)
    assert updated == {"a::t1", "a::t2"}  # grows monotonically
