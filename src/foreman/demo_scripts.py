"""Canned MockBackend scripts that drive the full pipeline offline (§11.5).

Each script mimics a real agent: it performs the file side effects a real agent
would (writing plan/adr/prd/issue/code files into the target repo or worktree),
then yields a realistic stream-json event sequence. They react to the prompt the
way a real agent would — e.g. the grill script removes its open question once it
sees reviewer comments, and the tdd script passes on the retry after a failure.

Used by ``foreman demo`` and by the pipeline/scheduler tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from .backend import RunSpec
from .paths import RepoPaths
from .stream_parser import StreamEvent, parse_event

# --------------------------------------------------------------------------- #
# Event builders
# --------------------------------------------------------------------------- #
def _init(spec: RunSpec) -> StreamEvent:
    return parse_event({
        "type": "system", "subtype": "init",
        "session_id": f"demo-{spec.kind}", "model": spec.model,
        "cwd": str(spec.cwd), "permissionMode": spec.permission_mode,
        "tools": ["Bash", "Edit", "Write", "Skill"], "skills": ["foreman-tdd"],
    })


def _assistant(text="", tool=None, usage=None) -> StreamEvent:
    content = []
    if tool:
        content.append({"type": "tool_use", "name": tool[0], "id": "t1", "input": tool[1]})
    if text:
        content.append({"type": "text", "text": text})
    return parse_event({"type": "assistant", "message": {
        "model": "demo", "content": content,
        "usage": usage or {"input_tokens": 1200, "output_tokens": 300}}})


def _result(cost=0.04, turns=2, result="") -> StreamEvent:
    return parse_event({"type": "result", "subtype": "success", "is_error": False,
                        "total_cost_usd": cost, "num_turns": turns, "result": result,
                        "terminal_reason": "completed",
                        "usage": {"input_tokens": 1200, "output_tokens": 300}})


# --------------------------------------------------------------------------- #
# Canned document bodies (a small "todo CLI" feature)
# --------------------------------------------------------------------------- #
PLAN_BODY = """\
# Implementation Plan: Add `done` command to the todo CLI

## Goal
Let users mark a todo item complete via `todo done <id>`, persisted to the store.

## Approach
Extend the existing command dispatcher with a `done` verb that flips an item's
`completed` flag through the store's public interface. No schema migration is
needed because the field already exists, defaulted to false.

## Seams & testing
Test at the store interface (`mark_done`, `get`) — the highest existing seam —
plus a thin CLI-level test that `todo done <id>` reports success.

## Risks
- Unknown id must error cleanly rather than create a phantom item.
"""

ADR_BODY = """\
# ADR: completion is a flag on the existing item, not a separate event log

## Open questions for reviewer

_None — all questions resolved from the codebase and prior decisions._

## Decision
We mark completion by setting a boolean `completed` flag on the existing todo
item rather than introducing a separate completion event log. The cost of an
event log is not justified for a single-user local CLI.
"""

PRD_BODY_V1 = """\
# PRD: `todo done` command

## Open questions for reviewer

- Should completing an already-completed item be an error or a silent no-op?

## Problem Statement
Users can add and list todos but cannot mark them done.

## Solution
A `todo done <id>` command marks the item complete.

## User Stories
1. As a user, I want to mark a todo done, so that it stops nagging me.
2. As a user, I want a clear error if the id does not exist, so that I can retry.

## User Flows
1. Mark done: given a todo with id 1, when I run `todo done 1`, then it is marked
   completed and the command reports success.

## Implementation Decisions
Add `mark_done(id)` to the store; the CLI dispatches `done` to it.

## Testing Decisions
Test `mark_done` at the store interface; one CLI-level test for the happy path.
Commands: test=`pytest`.

## Out of Scope
Un-completing an item.

## Further Notes
None.
"""

PRD_BODY_V2 = PRD_BODY_V1.replace(
    "## Open questions for reviewer\n\n"
    "- Should completing an already-completed item be an error or a silent no-op?\n",
    "## Open questions for reviewer\n\n"
    "_None — all questions resolved (re-completing is a silent no-op, per reviewer)._\n",
).replace("## Out of Scope\nUn-completing an item.",
          "## Out of Scope\nUn-completing an item. Re-completing is a silent no-op.")

ISSUE_001 = """\
---
id: ISS-001
title: Add mark_done to the store
status: queued
depends_on: []
branch: feature/todo-done/iss-001
attempts: 0
budget: { max_turns: 40, max_cost_usd: 2.0, timeout_min: 20 }
prd_refs: ["PRD §Implementation Decisions", "Story #1"]
acceptance_check: tests/test_store.py
touches: ["todo/store.py", "tests/test_store.py"]
kind: feature
---
## Goal
Add a `mark_done(item_id)` function to the store that sets an item's completed
flag and returns the updated item; raise KeyError for an unknown id.

## Acceptance criteria (testable)
- [ ] mark_done sets completed=True and the item is retrievable as completed
- [ ] mark_done on an unknown id raises KeyError

## Out of scope
- CLI wiring (separate slice)
"""

ISSUE_002 = """\
---
id: ISS-002
title: Wire `todo done` CLI command
status: queued
depends_on: ["ISS-001"]
branch: feature/todo-done/iss-002
attempts: 0
budget: { max_turns: 40, max_cost_usd: 2.0, timeout_min: 20 }
prd_refs: ["PRD §User Flows", "Story #1"]
acceptance_check: tests/test_cli.py
touches: ["todo/cli.py", "tests/test_cli.py"]
kind: feature
---
## Goal
Dispatch `done <id>` from the CLI to the store's mark_done and report success.

## Acceptance criteria (testable)
- [ ] run_cli(["done", "1"]) returns a success message after the item exists

## Out of scope
- Anything beyond the happy path
"""


# --------------------------------------------------------------------------- #
# Scripts
# --------------------------------------------------------------------------- #
async def initializer_script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
    """Mock one-time feature initializer (WS3.1): writes init.sh + feature-state.md."""
    import re
    paths = RepoPaths(spec.repo_root)
    yield _init(spec)
    yield _assistant(text="Bootstrapping the feature environment.")
    init_path = paths.init_script(spec.slug)
    fs_path = paths.feature_state_file(spec.slug)
    init_path.parent.mkdir(parents=True, exist_ok=True)
    init_path.write_text("#!/usr/bin/env bash\n# demo bootstrap\nset -e\nexit 0\n")
    fs_path.write_text(
        "# Feature state\n\n## Status\nReady to build.\n\n"
        "## Conventions\nSmall, deeply-tested vertical slices.\n\n"
        "## Gotchas\nUnknown ids must error cleanly.\n"
    )
    yield _assistant(tool=("Write", {"file_path": "init.sh"}), text="Wrote init.sh + feature-state.md.")
    yield _result(cost=0.02, turns=2)


async def planner_script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
    paths = RepoPaths(spec.repo_root)
    yield _init(spec)
    yield _assistant(text="Exploring the repo and drafting a plan.")
    # Mirror the real agent contract: write to the Foreman draft path, not canonical.
    path = paths.doc_draft_file(spec.slug, "plan")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PLAN_BODY)
    yield _assistant(tool=("Write", {"file_path": str(path)}), text="Wrote the plan draft.")
    yield _result(cost=0.06, turns=2)


async def grill_script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
    paths = RepoPaths(spec.repo_root)
    revising = "REVIEWER COMMENTS" in spec.prompt
    yield _init(spec)
    yield _assistant(text="Grilling the plan against the codebase.")
    # Mirror the real agent contract: write to the Foreman draft paths, not canonical.
    adr_draft = paths.doc_draft_file(spec.slug, "adr")
    prd_draft = paths.doc_draft_file(spec.slug, "prd")
    adr_draft.parent.mkdir(parents=True, exist_ok=True)
    adr_draft.write_text(ADR_BODY)
    prd_body = PRD_BODY_V2 if revising else PRD_BODY_V1
    if revising:
        prd_body = prd_body + "\n## Changelog\n\n- v2: resolved the re-completion question (no-op).\n"
    prd_draft.write_text(prd_body)
    yield _assistant(tool=("Write", {"file_path": str(adr_draft)}), text="Wrote ADR and PRD drafts.")
    yield _result(cost=0.08, turns=3)


async def slicer_script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
    paths = RepoPaths(spec.repo_root)
    idir = paths.issues_dir(spec.slug)
    idir.mkdir(parents=True, exist_ok=True)
    yield _init(spec)
    yield _assistant(text="Breaking the PRD into vertical slices.")
    (idir / "ISS-001.md").write_text(ISSUE_001)
    (idir / "ISS-002.md").write_text(ISSUE_002)
    yield _assistant(tool=("Write", {"file_path": "issues/ISS-001.md"}))
    yield _result(cost=0.05, turns=2)


# Worker code the tdd agent "writes" into its worktree, keyed by issue id.
WORKER_CODE = {
    "ISS-001": {
        "todo/store.py": '''\
"""Tiny in-memory todo store for the demo target project."""


class Store:
    def __init__(self):
        self._items = {}

    def add(self, item_id, text):
        self._items[item_id] = {"id": item_id, "text": text, "completed": False}
        return self._items[item_id]

    def get(self, item_id):
        return self._items[item_id]

    def mark_done(self, item_id):
        if item_id not in self._items:
            raise KeyError(item_id)
        self._items[item_id]["completed"] = True
        return self._items[item_id]
''',
        "tests/test_store.py": '''\
from todo.store import Store


def test_mark_done_completes_item():
    s = Store()
    s.add(1, "buy milk")
    s.mark_done(1)
    assert s.get(1)["completed"] is True


def test_mark_done_unknown_id_raises():
    import pytest
    s = Store()
    with pytest.raises(KeyError):
        s.mark_done(99)
''',
    },
    "ISS-002": {
        "todo/cli.py": '''\
"""CLI dispatcher for the demo target project."""

from todo.store import Store

_store = Store()
_store.add(1, "buy milk")


def run_cli(argv, store=None):
    store = store or _store
    if not argv:
        return "usage: todo <command>"
    cmd = argv[0]
    if cmd == "done":
        item = store.mark_done(int(argv[1]))
        return f"marked #{item['id']} done"
    return f"unknown command: {cmd}"
''',
        "tests/test_cli.py": '''\
from todo.cli import run_cli
from todo.store import Store


def test_done_command_reports_success():
    store = Store()
    store.add(1, "buy milk")
    msg = run_cli(["done", "1"], store=store)
    assert "done" in msg
''',
    },
}


def _write_worker_code(spec: RunSpec, files: dict) -> list[str]:
    written = []
    for rel, content in files.items():
        dest = spec.cwd / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        written.append(rel)
    return written


def _evidence_dir_from_prompt(spec: RunSpec):
    """Foreman names the run's evidence dir in the prompt; extract it (WS1.3)."""
    import re
    m = re.search(r"(\S*/runs/\S+/evidence)", spec.prompt)
    return Path(m.group(1)) if m else None


def _write_evidence(spec: RunSpec, *, passed: bool = True) -> list[str]:
    """Mimic a worker saving a test-log evidence artifact under runs/<id>/evidence/."""
    ed = _evidence_dir_from_prompt(spec)
    if ed is None:
        return []
    ed.mkdir(parents=True, exist_ok=True)
    (ed / "test.log").write_text("1 passed" if passed else "1 failed")
    return ["test.log"]


def _write_progress(spec: RunSpec, text: str = "Implemented the slice; tests green.") -> None:
    """Mimic a worker writing its mandatory progress.md handoff (WS3.2)."""
    ed = _evidence_dir_from_prompt(spec)
    if ed is None:
        return
    progress = ed.parent / "progress.md"
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text(f"# Progress\n\n{text}\n\n## Remaining\n- none\n")


def _summary_block(issue_id: str, files: list[str], passed: bool,
                   evidence: list[str] | None = None) -> str:
    import json
    obj = {
        "schema": "foreman-summary/v1",
        "issue_id": issue_id,
        "files_touched": files,
        "tests_added": [f for f in files if "test" in f],
        "commands": {
            "test": {"ran": True, "passed": passed,
                     "output_tail": "1 passed" if passed else "1 failed"},
        },
        "open_concerns": [],
        "escalate": False,
        "escalation_question": "",
        "evidence": evidence or [],
    }
    return "```json\n" + json.dumps(obj, indent=2) + "\n```"


def make_tdd_script(*, fail_first: bool = False):
    """Build a tdd worker script. If ``fail_first``, the first attempt writes a
    broken test (so Foreman's independent re-run fails) and the retry fixes it."""

    async def script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
        issue_id = spec.label
        files = dict(WORKER_CODE.get(issue_id, {}))
        is_retry = ("distilled failure report" in spec.prompt
                    or "PRIOR ATTEMPT FAILED" in spec.prompt)
        yield _init(spec)
        yield _assistant(text=f"Implementing {issue_id} with red-green-refactor.")
        if fail_first and not is_retry:
            # Write a deliberately failing test to prove Foreman re-runs and catches it.
            broken = dict(files)
            first_test = next((k for k in broken if "test" in k), None)
            if first_test:
                broken[first_test] = broken[first_test] + "\n\ndef test_intentionally_broken():\n    assert False\n"
            written = _write_worker_code(spec, broken)
            ev = _write_evidence(spec, passed=False)
            _write_progress(spec, "Wrote a (broken) test; needs a fix.")
            yield _assistant(tool=("Bash", {"command": "foreman-test"}), text="Tests written.")
            yield _assistant(text=_summary_block(issue_id, written, passed=True, evidence=ev))  # agent lies
            yield _result(cost=0.10, turns=4, result="")
            return
        written = _write_worker_code(spec, files)
        ev = _write_evidence(spec, passed=True)
        _write_progress(spec, "Implemented the slice; tests green.")
        yield _assistant(tool=("Bash", {"command": "foreman-test"}), text="Green.")
        yield _assistant(text=_summary_block(issue_id, written, passed=True, evidence=ev))
        yield _result(cost=0.12, turns=5, result="")

    return script


E2E_TEST = '''\
"""End-to-end flow test derived from the PRD user flows."""

from todo.cli import run_cli
from todo.store import Store


def test_user_can_mark_a_todo_done_end_to_end():
    store = Store()
    store.add(1, "buy milk")
    msg = run_cli(["done", "1"], store=store)
    assert "done" in msg
    assert store.get(1)["completed"] is True
'''


def _verdict_block(issue_id: str, *, verdict="pass", objections=None, scores=5) -> str:
    import json
    obj = {
        "schema": "foreman-verdict/v1",
        "issue_id": issue_id,
        "verdict": verdict,
        "scores": {dim: {"score": scores, "justification": f"{dim} looks good"}
                   for dim in ("functionality", "prd_fidelity", "craft", "test_honesty")},
        "objections": objections or [],
        "summary": "demo evaluator verdict",
    }
    return "```json\n" + json.dumps(obj, indent=2) + "\n```"


def make_evaluator_script(*, verdict="pass", objections=None, scores=5):
    """A mock read-only evaluator. Defaults to a passing verdict."""
    async def script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
        issue_id = spec.label.replace("-eval", "")
        yield _init(spec)
        yield _assistant(text="Grading the slice against acceptance criteria + PRD.")
        yield _assistant(text=_verdict_block(issue_id, verdict=verdict,
                                             objections=objections, scores=scores))
        yield _result(cost=0.01, turns=2)
    return script


async def janitor_script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
    """Mock specialist janitor (WS4.3): a tiny behaviour-preserving change + handoff."""
    yield _init(spec)
    yield _assistant(text=f"Janitor {spec.label}: small, behaviour-preserving cleanup.")
    # A unique-per-janitor doc note so sequential janitor merges never collide.
    (spec.cwd / f"JANITOR_{spec.label}.md").write_text(f"Janitor {spec.label} pass notes.\n")
    _write_progress(spec, "Janitor pass complete; suite still green.")
    yield _assistant(tool=("Bash", {"command": "foreman-test"}), text="Suite still green.")
    yield _assistant(text=_summary_block(spec.label, [f"JANITOR_{spec.label}.md"],
                                         passed=True, evidence=[]))
    yield _result(cost=0.03, turns=2)


def _audit_block(requirements=None) -> str:
    import json
    obj = {
        "schema": "foreman-audit/v1",
        "requirements": requirements or [
            {"requirement": "User can mark a todo done", "status": "satisfied",
             "evidence": "tests/test_e2e_flow.py", "note": ""},
        ],
        "summary": "demo audit — implementation matches the PRD",
    }
    return "```json\n" + json.dumps(obj, indent=2) + "\n```"


def make_auditor_script(*, requirements=None):
    """Mock read-only spec-integrity auditor (WS5.1). Defaults to all-satisfied."""
    async def script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
        yield _init(spec)
        yield _assistant(text="Auditing the merged feature against the approved PRD.")
        yield _assistant(text=_audit_block(requirements))
        yield _result(cost=0.01, turns=2)
    return script


async def e2e_script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
    yield _init(spec)
    yield _assistant(text="Deriving e2e tests from the PRD user flows.")
    dest = spec.cwd / "tests" / "test_e2e_flow.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(E2E_TEST)
    ev = _write_evidence(spec, passed=True)
    yield _assistant(tool=("Write", {"file_path": "tests/test_e2e_flow.py"}), text="Wrote e2e test.")
    yield _assistant(text=_summary_block("e2e", ["tests/test_e2e_flow.py"], passed=True, evidence=ev))
    yield _result(cost=0.07, turns=3)


def demo_scripts(*, fail_first_issue: str | None = None) -> dict:
    """The default registry for the demo. Optionally make one issue fail first."""
    scripts = {
        "initializer": initializer_script,
        "planner": planner_script,
        "grill": grill_script,
        "slicer": slicer_script,
        "tdd": make_tdd_script(fail_first=False),
        "janitor": janitor_script,
        "evaluator": make_evaluator_script(verdict="pass"),
        "auditor": make_auditor_script(),
        "e2e": e2e_script,
    }
    if fail_first_issue:
        scripts[f"tdd:{fail_first_issue}"] = make_tdd_script(fail_first=True)
    return scripts
