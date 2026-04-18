"""WS1.3/1.5 — the worktree deny hook, the foreman-test wrapper, and the installer."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from foreman import hooks
from foreman.hooks import installer

ASSETS = installer.ASSETS


def _run_hook(event: dict):
    proc = subprocess.run(
        [sys.executable, str(ASSETS / "deny_protected.py")],
        input=json.dumps(event), capture_output=True, text=True,
    )
    return proc


def test_hook_denies_write_to_verification_json():
    proc = _run_hook({"tool_name": "Write",
                      "tool_input": {"file_path": "/repo/.foreman/features/f/verification.json"}})
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_denies_edit_to_issue_file():
    proc = _run_hook({"tool_name": "Edit",
                      "tool_input": {"file_path": "/r/.foreman/features/f/issues/ISS-001.md"}})
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_denies_write_to_check_dir():
    proc = _run_hook({"tool_name": "Write",
                      "tool_input": {"file_path": "/r/tests/ISS-001.check/test_x.py"}})
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_allows_normal_write():
    proc = _run_hook({"tool_name": "Write", "tool_input": {"file_path": "/repo/src/app.py"}})
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""  # no deny emitted


def test_hook_blocks_bash_redirect_into_protected_via_exit2():
    proc = _run_hook({"tool_name": "Bash",
                      "tool_input": {"command": "echo '{}' > .foreman/features/f/verification.json"}})
    assert proc.returncode == 2
    assert "Foreman" in proc.stderr


def test_hook_allows_innocuous_bash():
    proc = _run_hook({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}})
    assert proc.returncode == 0


# --- foreman-test wrapper --- #

def _tiny_pytest_project(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_pass_one():\n    assert True\n\n"
        "def test_pass_two():\n    assert True\n\n"
        "def test_fail_one():\n    assert False\n"
    )
    return tmp_path


def test_foreman_test_console_log_and_trailer(tmp_path):
    proj = _tiny_pytest_project(tmp_path)
    env = dict(os.environ)
    env["FOREMAN_TEST_CMD"] = f"{sys.executable} -m pytest"
    env["FOREMAN_TEST_LOG"] = str(proj / ".foreman-test.log")
    proc = subprocess.run(
        [sys.executable, str(ASSETS / "foreman-test")],
        cwd=proj, env=env, capture_output=True, text=True,
    )
    # Console is short and reports counts + failures.
    console = proc.stdout.splitlines()
    assert len([l for l in console if not l.startswith("FOREMAN-TEST-RESULTS")]) <= 20
    assert any("2 passed, 1 failed" in l for l in console)
    # Authoritative trailer parses with passed + failed node ids.
    trailer = [l for l in console if l.startswith("FOREMAN-TEST-RESULTS")]
    assert trailer
    obj = json.loads(trailer[0].split(" ", 1)[1])
    assert any("test_fail_one" in f for f in obj["failed"])
    assert len(obj["passed"]) == 2
    # Full log has greppable ERROR lines.
    log = (proj / ".foreman-test.log").read_text()
    assert "ERROR" in log and "test_fail_one" in log
    assert proc.returncode != 0  # underlying suite failed


def test_foreman_test_fast_subsample_is_deterministic(tmp_path):
    proj = _tiny_pytest_project(tmp_path)
    # Add more tests so a 1/3 sample is meaningfully smaller.
    (proj / "test_more.py").write_text(
        "\n".join(f"def test_n{i}():\n    assert True\n" for i in range(9))
    )
    env = dict(os.environ)
    env["FOREMAN_TEST_CMD"] = f"{sys.executable} -m pytest"
    env["FOREMAN_WORKER_ID"] = "ISS-007"

    def run_fast():
        return subprocess.run(
            [sys.executable, str(ASSETS / "foreman-test"), "--fast"],
            cwd=proj, env=env, capture_output=True, text=True,
        ).stdout

    out1, out2 = run_fast(), run_fast()
    assert "--fast" in out1

    def _selection(out):
        # Drop the header line (carries varying elapsed time); keep counts/failures
        # and the "N sampled, seed=" note — the deterministic selection.
        import re
        note = re.search(r"\(--fast: (\d+) sampled, seed=(\d+)\)", out)
        body = [l for l in out.splitlines()[1:]]
        return (note.groups() if note else None, body)

    # Same seed (worker id) ⇒ identical selection ⇒ identical sampled set.
    assert _selection(out1) == _selection(out2)
    # A --fast run must NOT emit the authoritative trailer (would shrink baseline).
    assert "FOREMAN-TEST-RESULTS" not in out1


# --- installer --- #

def test_installer_writes_settings_and_env(tmp_path):
    worktree = tmp_path / "ISS-001"
    worktree.mkdir()
    inst = hooks.install(worktree, test_command="pytest -q", worker_id="ISS-001")
    assert inst.settings_path.exists()
    settings = json.loads(inst.settings_path.read_text())
    assert "PreToolUse" in settings["hooks"] and "Stop" in settings["hooks"]
    # foreman-test on PATH + the real command injected.
    assert str(inst.hooks_dir) in inst.env["PATH"]
    assert inst.env["FOREMAN_TEST_CMD"] == "pytest -q"
    assert (inst.hooks_dir / "foreman-test").exists()
    # Sibling dir, never inside the worktree (no diff pollution).
    assert inst.hooks_dir.parent == worktree.parent
    assert not str(inst.hooks_dir).startswith(str(worktree) + os.sep)

    hooks.cleanup(worktree)
    assert not inst.hooks_dir.exists()


# --- MCP tools (the worker may edit/run via MCP equivalents, e.g. lean-ctx) --- #

def test_hook_denies_mcp_edit_to_protected_path():
    """An MCP edit tool (ctx_edit -> {path, old_string, new_string}) writing a
    Foreman-owned file must be denied, just like the native Edit."""
    proc = _run_hook({"tool_name": "mcp__lean-ctx__ctx_edit",
                      "tool_input": {"path": "/r/.foreman/features/f/verification.json",
                                     "old_string": "a", "new_string": "b"}})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_denies_mcp_edit_to_issue_file():
    proc = _run_hook({"tool_name": "mcp__lean-ctx__ctx_edit",
                      "tool_input": {"path": "/r/.foreman/features/f/issues/ISS-002.md",
                                     "old_string": "a", "new_string": "b"}})
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_blocks_mcp_shell_writing_protected_file():
    """An MCP shell tool (ctx_shell -> {command}) redirecting into a protected file
    is blocked via exit 2, like native Bash."""
    proc = _run_hook({"tool_name": "mcp__lean-ctx__ctx_shell",
                      "tool_input": {"command": "echo x > .foreman/features/f/verification.json"}})
    assert proc.returncode == 2


def test_hook_allows_mcp_read_of_protected_file():
    """Reading a protected file is fine — only WRITES are blocked. An MCP read tool
    (ctx_read -> {path}) with no write markers must NOT be denied."""
    proc = _run_hook({"tool_name": "mcp__lean-ctx__ctx_read",
                      "tool_input": {"path": "/r/.foreman/features/f/verification.json"}})
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""          # no deny emitted


def test_hook_allows_mcp_edit_of_unprotected_file():
    proc = _run_hook({"tool_name": "mcp__lean-ctx__ctx_edit",
                      "tool_input": {"path": "/r/app/main.py", "old_string": "a", "new_string": "b"}})
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_installed_settings_matcher_covers_mcp_tools(tmp_path):
    """The per-worktree PreToolUse hook must be registered for MCP tools too, or the
    deny hook never fires for an MCP edit/shell (the worker could bypass the gate)."""
    wt = tmp_path / "ISS-001"
    wt.mkdir()
    inst = hooks.install(wt, test_command="pytest", worker_id="ISS-001")
    settings = json.loads(Path(inst.settings_path).read_text())
    matcher = settings["hooks"]["PreToolUse"][0]["matcher"]
    assert "mcp__" in matcher
    assert "Edit" in matcher and "Bash" in matcher   # native still covered
