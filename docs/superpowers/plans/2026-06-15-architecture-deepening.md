# Architecture Deepening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This is a **behaviour-preserving refactor**: the existing 290-test suite is the primary regression net — it must stay green at every commit. New modules get new unit tests written test-first.

**Goal:** Deepen Foreman's shallow modules and close leaking seams per the architecture review (`/tmp/architecture-review-20260615-071505.html`), so behaviour is unchanged but locality, leverage, and testability improve.

**Architecture:** Eight deepenings, sequenced low-risk → high-risk so each builds on a cleaner base. New deep modules (`seal.py`, `prompts.py`, `verification/merge_gate.py`, `issue_run.py`) absorb logic currently smeared across `scheduler.py` (1048 L), `state.py`, `runner.py`, `pipeline.py`, and the TUI. `scheduler.py` shrinks to a dispatcher; `FileStore` becomes the sole owner of the `.foreman/` layout.

**Tech Stack:** Python 3.13, dataclasses + enums, `pytest`/`pytest-asyncio` (`asyncio_mode=auto`), Textual TUI, `git`/`claude` via asyncio subprocess. `AgentBackend` seam with `MockBackend` for offline tests. Run tests with `.venv/bin/python -m pytest`.

**Invariants that MUST hold across every cycle (DECISIONS.md):**
- §2 The `AgentBackend` seam stays the single seam; two adapters (Claude/Mock).
- §4/R3 Approval = sha256 of body, auto-invalidates on edit (the Seal).
- WS1/WS2 Foreman re-runs the gate itself and never trusts the agent; the evaluator stays a **separate read-only `--agent`** (structural builder/grader separation).
- R4 All durable state is rebuildable from `.foreman/` files.

**Commit discipline:** one commit per completed task (failing-test + impl + green). Commit subject: `refactor(<area>): <what> (deepening N)`. Do NOT bump the package version or touch CHANGELOG until the final review.

**Execution order (cycles):** 1·Seal → 2·Prompts → 3·Budget → 4·MergeGate → 5·IssueRun → 6·FileStore layout → 7·Controller facade → 8·StreamEvent vocabulary.

**Per-cycle protocol (Plan → Review → Implement → Test → Review):**
1. Read the live source of every file the cycle touches (it may have shifted from earlier cycles).
2. Write the new module's failing unit test(s).
3. Implement the deep module; move logic behind it.
4. Update call sites.
5. Run the touched module's tests, then the full suite. Green = commit.
6. Self-review the diff (`requesting-code-review` skill) before moving on.

---

## Cycle 1 — The Seal (deepening 5) · `Strong`

**Problem:** "approval = sha256(body), auto-invalidate on edit" is re-implemented in `state._load_doc`/`approve_doc` and again in `retro/driver.load`/`approve`. Two adapters, one invariant, no shared module.

**Files:**
- Create: `src/foreman/seal.py`
- Create: `tests/test_seal.py`
- Modify: `src/foreman/state.py:18` (import), `:201-221` (`_load_doc`), `:249-265` (`approve_doc`)
- Modify: `src/foreman/retro/driver.py:20` (import), `:98-127` (`load`/`approve`)
- Keep: `src/foreman/hashing.py` (`body_hash` stays the low-level primitive; `seal.fingerprint` delegates to it so existing imports/tests are untouched)

**Target interface (`seal.py`):**

```python
"""The hash-seal — the one place the approval invariant lives (R3, DECISIONS §4).

A *seal* binds a reviewer's approval to the exact body they approved: a SHA-256
fingerprint of the normalized body. On every load, a stored fingerprint that no
longer matches the current body means the body changed after approval, so the
seal is broken and approval must revert. Two adapters use this: gated documents
(plan/adr/prd via FileStore) and retro proposals (retro/driver)."""

from __future__ import annotations
from .hashing import body_hash

def fingerprint(body: str) -> str:
    """Canonical seal fingerprint of a document body."""
    return body_hash(body)

def intact(stored_fingerprint: str | None, body: str) -> bool:
    """True iff a seal was recorded AND still matches the current body."""
    return bool(stored_fingerprint) and stored_fingerprint == fingerprint(body)
```

- [ ] **Step 1.1 — Write failing test** `tests/test_seal.py`:

```python
from foreman import seal
from foreman.hashing import body_hash

def test_fingerprint_matches_body_hash():
    assert seal.fingerprint("# Plan\nbody\n") == body_hash("# Plan\nbody\n")

def test_intact_true_when_unchanged():
    body = "# Plan\nbody\n"
    assert seal.intact(seal.fingerprint(body), body) is True

def test_intact_false_when_body_edited():
    body = "# Plan\nbody\n"
    fp = seal.fingerprint(body)
    assert seal.intact(fp, body + "edited\n") is False

def test_intact_false_when_no_seal():
    assert seal.intact(None, "anything") is False
    assert seal.intact("", "anything") is False

def test_intact_ignores_trailing_whitespace_and_crlf():
    # body_hash normalizes CRLF + trailing whitespace; seal inherits that.
    assert seal.intact(seal.fingerprint("a\nb\n"), "a\r\nb\n   ") is True
```

- [ ] **Step 1.2 — Run, expect FAIL** (`no module named foreman.seal`):
  `.venv/bin/python -m pytest tests/test_seal.py -q`
- [ ] **Step 1.3 — Implement `seal.py`** exactly as the interface above.
- [ ] **Step 1.4 — Run, expect PASS.**
- [ ] **Step 1.5 — Refactor `state.py`** to call `seal`:
  - `_load_doc` (currently `:215-216`): replace the inline check
    `if approval is None or approval.body_sha256 != body_hash(parsed.body):`
    with `if not seal.intact(approval.body_sha256 if approval else None, parsed.body):`.
  - `approve_doc` (`:262`): `body_sha256=seal.fingerprint(gd.body)`.
  - Add `from . import seal` (keep the `body_hash` import only if still used elsewhere; remove if now unused to keep imports honest).
- [ ] **Step 1.6 — Refactor `retro/driver.py`** to call `seal`:
  - `load` (`:110`): `sealed = status == "approved" and seal.intact(approval_hash, doc.body)`.
  - `approve` (`:125`): `doc.meta["body_sha256"] = seal.fingerprint(doc.body)`.
  - Add `from ..hashing import body_hash` → replace with `from .. import seal`; drop the now-unused `body_hash` import.
- [ ] **Step 1.7 — Run focused tests, expect PASS:**
  `.venv/bin/python -m pytest tests/test_seal.py tests/test_state.py tests/test_retro.py -q`
- [ ] **Step 1.8 — Full suite, expect 290+ pass:** `.venv/bin/python -m pytest -q`
- [ ] **Step 1.9 — Commit:** `git add -A && git commit -m "refactor(seal): one hash-seal module for docs + retro proposals (deepening 5)"`

**Wins:** locality (one invariant); two adapters justify the seam; normalization rule fixed once.

---

## Cycle 2 — PromptAssembler (deepening 3) · `Worth exploring`

**Problem:** "turn a phase+context into a prompt" is smeared across `skill_invocation.py`, `context/assembler.py`, `janitor.py`, `agents/evaluator.py`, `audit.py`, `context/initializer.py`, plus continuation/extension text built inline in `scheduler.py` (`:415-422`, `:714-717`, `:970-972`, `:1012-1014`) and `pipeline.py`. A dead `SkillInvocation.tdd()` still ships.

**Approach (conservative — consolidate the *scattered* parts, do not merge the well-owned per-role builders):**
The deep win here is a single owner for the **continuation/extension text** and a single **prompt-decoration** point, plus deleting dead code. The per-role base builders (`assembler.worker_prompt`, `evaluator.build_prompt`, `audit.build_prompt`, `initializer.build_prompt`, `janitor.build_prompt`) already each own one prompt and are individually deep — collapsing them into one mega-module would *reduce* locality. Instead:

**Files:**
- Create: `src/foreman/prompts.py` (continuation/decoration owner)
- Create: `tests/test_prompts.py`
- Modify: `src/foreman/scheduler.py` (replace 4 inline continuation strings with `prompts.*`)
- Modify: `src/foreman/pipeline.py` (replace its inline extension continuation with `prompts.*`)
- Modify: `src/foreman/skill_invocation.py` (delete dead `tdd()` — verify via grep it is unreferenced first)

**Target interface (`prompts.py`):**

```python
"""Prompt decoration — the one place continuation/extension text is composed.

Per-role base prompts live with their agents (assembler/evaluator/audit/janitor/
initializer). This module owns the cross-cutting text that the orchestrator used
to inline: the 'CONTINUE — resumed your session with more turns' wrappers and the
distilled-failure-report appendix."""

from __future__ import annotations

def worker_continuation(ext_turns: int) -> str: ...
def agent_continuation(what: str) -> str:   # what = "grading this slice" / "the audit" / "the e2e flow"
    ...
def with_failure_report(prompt: str, failure_report: str) -> str: ...
```

- [ ] **Step 2.1 — Read live source:** `skill_invocation.py`, `context/assembler.py`, `pipeline.py` (extension loop), and confirm `SkillInvocation.tdd` is dead: `.venv/bin/python -m pytest --collect-only -q >/dev/null; grep -rn "\.tdd(" src tests`.
- [ ] **Step 2.2 — Write `tests/test_prompts.py`** asserting each helper returns text containing the key directive ("RESUMED", "more turns", "do NOT restart"/"Do not start over"), is idempotent-safe, and that `with_failure_report` no-ops on empty input.
- [ ] **Step 2.3 — Run, expect FAIL.**
- [ ] **Step 2.4 — Implement `prompts.py`**, lifting the exact existing strings from `scheduler.py:415-422` (worker), `:714-717`/`:970-972`/`:1012-1014` (agent), and the janitor failure-report appendix (`scheduler.py:400-401`).
- [ ] **Step 2.5 — Run, expect PASS.**
- [ ] **Step 2.6 — Replace inline strings** in `scheduler.py` and `pipeline.py` with calls to `prompts.*`. Behaviour identical (same text).
- [ ] **Step 2.7 — Delete dead `SkillInvocation.tdd()`** only if Step 2.1 confirmed it is unreferenced.
- [ ] **Step 2.8 — Focused + full suite green.**
- [ ] **Step 2.9 — Commit:** `refactor(prompts): single owner for continuation text; drop dead tdd builder (deepening 3)`

**Wins:** locality (continuation text in one place); delete dead builder; prompt decoration testable without a backend.

---

## Cycle 3 — Budget policy behind the runner (deepening 7) · `Worth exploring`

**Problem:** `runner.py` hard-kills on turns/cost/timeout/stuck, but the "extend → resume → give up" policy that reads `terminal_reason` is duplicated in `scheduler._work_issue` (`:460-489`), `scheduler._run_agent_with_extensions` (`:640-689`), and `pipeline.py`'s extension loop. Terminal-reason constants re-interpreted in 5 sites.

**Approach:** Extract the **agent-with-extensions** loop (already generalised in `_run_agent_with_extensions`) into a reusable deep helper that both the scheduler's non-worker agents AND pipeline use. The worker loop in `_work_issue` is special (it interleaves the gate), so it keeps its own extension branch for now — but the *decision predicate* ("should this terminal reason extend?") moves into one function both share.

**Files:**
- Modify: `src/foreman/runner.py` — add a pure predicate `should_extend(terminal_reason, *, has_session, extensions, max_extensions, auto_extend) -> bool` and (optional) a `RunResult.is_turn_cutoff` property.
- Create: `tests/test_runner.py` additions (or new `test_budget_policy.py`) covering `should_extend` truth table.
- Modify: `src/foreman/scheduler.py` — `_work_issue` (`:468`) and `_run_agent_with_extensions` (`:679-682`) both call `runner.should_extend(...)`.
- Modify: `src/foreman/pipeline.py` — its extension loop calls the same predicate.

**Target (in `runner.py`):**

```python
def should_extend(
    terminal_reason: str, *, has_session: bool, extensions: int,
    max_extensions: int, auto_extend: bool, requested_more: bool = False,
) -> bool:
    """True iff a turn cut-off (or explicit request) should resume the SAME
    session with more turns rather than escalate. Cost/timeout/stuck never extend."""
    if not auto_extend or not has_session or extensions >= max_extensions:
        return False
    return requested_more or terminal_reason == KILLED_TURNS
```

- [ ] **Step 3.1 — Read live `runner.py`, `pipeline.py`** extension loops.
- [ ] **Step 3.2 — Write truth-table test** for `should_extend` (KILLED_TURNS+budget → True; KILLED_COST → False; no session → False; extensions==max → False; requested_more on non-turn kill → True only if session+budget).
- [ ] **Step 3.3 — Run, expect FAIL.**
- [ ] **Step 3.4 — Implement `should_extend`.**
- [ ] **Step 3.5 — Run, expect PASS.**
- [ ] **Step 3.6 — Replace the duplicated predicates** at the 3 sites with `should_extend(...)`. Keep the surrounding logging/escalation identical.
- [ ] **Step 3.7 — Focused (`test_runner.py`, `test_scheduler.py`, `test_pipeline.py`) + full suite green.**
- [ ] **Step 3.8 — Commit:** `refactor(runner): one extend-vs-escalate predicate shared by scheduler+pipeline (deepening 7)`

**Wins:** locality (terminal-reason policy in one place); a new kill reason is one edit.

---

## Cycle 4 — MergeGate: one sealed verdict (deepening 1) · `Strong`

**Problem:** the compound verdict is shallow — `verification/gate.py:run_gate` returns only the structural half (`gate.passed`); the evaluator stage and the bounce/escalate policy live as loop-local state (`eval_bounces`, `attempts`) in `scheduler._work_issue:542-595`. The verdict can't be tested through one interface; test fixtures duplicate the scheduler's wiring.

**Approach:** A new deep module `verification/merge_gate.py` exposing `decide(...) -> GateDecision`, where `GateDecision.action ∈ {MERGE, BOUNCE, ESCALATE}` plus `report` (distilled failure text for a bounce) and `reason` (escalation/bounce summary). It runs `run_gate` and, when structurally passing and the evaluator is enabled, runs the evaluator (via an injected async callable) and applies the bounce/escalate policy. The scheduler keeps owning side effects (status writes, worktree removal, commits, escalation file) but **the decision** moves behind one interface. The evaluator stays a separate read-only `--agent` (DECISIONS §2/WS2) — `merge_gate` calls an injected `evaluate` coroutine, it does **not** spawn agents itself.

**Files:**
- Create: `src/foreman/verification/merge_gate.py`
- Create: `tests/test_merge_gate.py`
- Modify: `src/foreman/scheduler.py:531-608` (`_work_issue` gate block) to call `merge_gate.decide(...)` and switch on the action.

**Target interface (`merge_gate.py`):**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Optional
from ..models import Issue
from .gate import GateResult, run_gate
from ..agents import evaluator as evaluator_mod

class Action(str, Enum):
    MERGE = "merge"        # structural gate + evaluator both passed
    BOUNCE = "bounce"      # retry a fresh builder with `report`
    ESCALATE = "escalate"  # hand to a human with `reason`

@dataclass
class GateDecision:
    action: Action
    gate: GateResult
    verdict: Optional[evaluator_mod.Verdict] = None
    reason: str = ""          # bounce/escalate one-liner
    report: str = ""          # distilled failure text for a fresh retry
    outcome: str = ""         # metrics label hint: "" | "evaluator_bounce" | "escalated:<why>"
    is_evaluator_bounce: bool = False

async def decide(
    *, issue: Issue, worktree: Path, commands, check_dir, evidence_dir, baseline_path,
    summary_evidence, env, timeout_s: float,
    attempts: int, max_retries: int, eval_bounces: int,
    evaluator_enabled: bool,
    on_structural_pass: Callable[[], Awaitable[None]],
    evaluate: Optional[Callable[[GateResult], Awaitable[Optional[evaluator_mod.Verdict]]]] = None,
    distill: Callable[..., str],
) -> GateDecision:
    """Run the full merge gate and return ONE decision. Side effects are limited to
    the gate's own subprocess runs, the injected `on_structural_pass` (the commit,
    so the evaluator sees a real diff and the slice is mergeable), and the injected
    `evaluate` agent spawn."""
```

Policy inside `decide` (lifted verbatim from `_work_issue`):
- gate fails → `BOUNCE` with `report=distill(...)`, unless `attempts >= max_retries` → `ESCALATE` (`reason=gate.reason + feedback`).
- **gate passes structurally → `await on_structural_pass()` FIRST** (this is the `git_ops.commit_all` at the old `scheduler.py:544`; the evaluator diffs the committed worktree at `_evaluate`→`git_ops.diff_against`, so the commit MUST precede `evaluate`). Then:
  - evaluator disabled → `MERGE`.
  - evaluator enabled → call `evaluate(gate)`:
    - `None`/uncertain → `ESCALATE` (`reason="evaluator could not decide…"`).
    - not `is_pass` → `BOUNCE` (`is_evaluator_bounce=True`, `report=distill(verdict.feedback())`); if `eval_bounces+1 >= 2 or attempts+1 >= max_retries` → `ESCALATE`.
    - pass → `MERGE`.

> **[Review BLOCKER fix]** The commit must happen between the structural gate passing and the evaluator running — never after `decide` returns. It is therefore an injected `on_structural_pass` callback that `decide` awaits the instant `gate.passed` is true. The scheduler's `AWAITING_EVALUATION` status write stays in the scheduler's `evaluate` wrapper (it runs only when `evaluator_enabled`).

- [ ] **Step 4.1 — Read live `verification/gate.py`, `agents/evaluator.py`** (done) and the exact `_work_issue` block `:531-634`.
- [ ] **Step 4.2 — Write `tests/test_merge_gate.py`** (the new single test surface). Use a fake `GateResult` by monkeypatching `merge_gate.run_gate` with an async stub, an injected `evaluate` stub, a trivial `distill`, and a `commits=[]`-recording async `on_structural_pass` stub. Assert `on_structural_pass` is awaited exactly once on every gate-passing path (MERGE and evaluator-BOUNCE/ESCALATE) and never when the gate fails structurally. Cases:
  - gate fails, attempts<max → `BOUNCE`, report non-empty.
  - gate fails, attempts==max-1 (so attempts+1>=max) → `ESCALATE`.
  - gate passes, evaluator disabled → `MERGE`.
  - gate passes, verdict pass → `MERGE`.
  - gate passes, verdict objections, eval_bounces 0 → `BOUNCE` + `is_evaluator_bounce`.
  - gate passes, verdict objections, eval_bounces 1 → `ESCALATE`.
  - gate passes, verdict uncertain/None → `ESCALATE`.
- [ ] **Step 4.3 — Run, expect FAIL.**
- [ ] **Step 4.4 — Implement `merge_gate.decide`** lifting the policy verbatim.
- [ ] **Step 4.5 — Run, expect PASS.**
- [ ] **Step 4.6 — Rewire `scheduler._work_issue`:** replace `:531-634` so it builds the gate inputs and calls:
  ```python
  async def _on_pass():
      await git_ops.commit_all(wt, f"{issue.id}: {issue.title}")
  async def _evaluate_cb(g):
      self.store.update_issue_status(slug, issue.id, IssueStatus.AWAITING_EVALUATION)
      return await self._evaluate(slug, issue, wt, g, evidence_dir)
  decision = await merge_gate.decide(
      issue=issue, worktree=wt, commands=commands,
      check_dir=self.store.paths.issue_check_dir(slug, issue.id),
      evidence_dir=evidence_dir, baseline_path=self.store.paths.baseline_file(slug),
      summary_evidence=summary_evidence, env=hookinst.env, timeout_s=self.verify_timeout_s,
      attempts=issue.attempts, max_retries=self.config.limits.max_retries,
      eval_bounces=eval_bounces, evaluator_enabled=self.config.evaluator_enabled,
      on_structural_pass=_on_pass, evaluate=_evaluate_cb, distill=distiller.distill)
  ```
  then switches on `decision.action`:
  - `MERGE`: `mark_issue_passed(evidence=decision.gate.evidence_artifacts)` → `_land` → status → stamp `label_success` → remove wt → return "done".
  - `BOUNCE`: bump attempts, status TESTS_FAILING, set `failure_report=decision.report`; if `decision.is_evaluator_bounce` then `eval_bounces += 1` and stamp `evaluator_bounce`; `continue`.
  - `ESCALATE`: `_escalate(decision.reason)`, stamp `escalated(decision.outcome or decision.reason)`, remove wt, return "escalated".
  - The `AWAITING_EVALUATION` status write moves INTO the `_evaluate_cb` wrapper (above), so it fires only on the evaluator path, exactly as today. `_evaluate`/`_write_verdict`/monitor calls stay in the scheduler; `decide` reaches them only through the injected `evaluate`.
  - Note: `eval_bounces` is still owned by the `_work_issue` loop and passed INTO `decide` each iteration; `decide` returns whether this was an evaluator bounce so the loop can increment it. This keeps `decide` free of loop-carried state.
- [ ] **Step 4.7 — Focused (`test_merge_gate.py`, `test_gate.py`, `test_scheduler.py`, `test_evaluator.py`, `test_integration_ws56.py`) + full suite green.**
- [ ] **Step 4.8 — Commit:** `refactor(verification): MergeGate.decide() returns one sealed verdict (deepening 1)`

**Wins:** interface is the test surface again; bounce policy in one module; scheduler block drops ~80 lines. Respects §2/WS2 (evaluator still a separate read-only agent, injected).

---

## Cycle 5 — IssueRun: lift the issue lifecycle out of the scheduler (deepening 2) · `Strong`

**Problem:** `_work_issue` (`:321-638`, 317 L) holds lock lifecycle, prompt assembly, the runner+extension loop, handoff check, the merge gate, retry/escalation, and worktree teardown behind a `(slug, issue) -> str` interface. Untestable except by running all of Phase A + scripting the whole backend.

**Approach:** Create `IssueRun` — a deep module owning one issue's lifecycle behind `run() -> Outcome`. The scheduler keeps dispatch (`pick_dispatch`, the `while True` loop, janitor cadence, e2e/audit) and constructs an `IssueRun` per dispatched issue. `IssueRun` receives the already-built collaborators (store, config, runner, worktrees, locks helpers, assembler, ledger, monitor, merge_gate). This is the largest cycle — do it AFTER Cycles 1–4 so it consumes the clean `merge_gate`, `prompts`, and `should_extend`.

**Files:**
- Create: `src/foreman/issue_run.py`
- Create: `tests/test_issue_run.py`
- Modify: `src/foreman/scheduler.py` — `_work_issue` becomes a thin `IssueRun(...).run()`; move `_run_agent_with_extensions`-for-worker, the handoff check, gate wiring, and retry loop into `IssueRun`. Keep `_evaluate`, `_escalate`, `_land`, `_merge`, `_stamp_outcome` callable by `IssueRun` (inject them or move the small ones).

**Target interface (`issue_run.py`):**

```python
@dataclass
class Outcome:
    status: str   # "done" | "escalated" | "killed" | "blocked"

class IssueRun:
    def __init__(self, *, slug, issue, deps: SchedulerDeps,
                 reviewer_answer=None, janitor_kind=None): ...
    async def run(self) -> Outcome: ...
```

where `SchedulerDeps` is a small dataclass bundling what the run needs (store, config, runner, worktrees, ledger, monitor, assembler, run_id_clock, verify_timeout_s, cancels registry, lock_blocked set, callbacks `escalate`, `evaluate`, `stamp_outcome`, `land`). The exact set is finalised against live code at Step 5.1.

- [ ] **Step 5.1 — Read live `scheduler.py`** post-Cycle-4 (line numbers will have shifted). Map every external call `_work_issue` makes (`self.store.*`, `self.worktrees.*`, `locks.*`, `hooks.*`, `vendored.*`, `agents_installer.*`, `self.assembler.*`, `self.runner.*`, `self.ledger.*`, `merge_gate.decide`, `self._evaluate/_escalate/_land/_stamp_outcome`, `prompts.*`, `initializer.*`, `distiller.*`, `janitor_mod.*`). This list defines `SchedulerDeps`.
- [ ] **Step 5.2 — Write `tests/test_issue_run.py`.** `IssueRun.run()` still needs a real git repo (`run_gate` runs the suite; `worktrees.integration_worktree()`/`ensure_base()` need git). The win over `test_scheduler.py` is skipping the planner→grill→slicer agents, NOT skipping the repo. So: reuse `create_sample_repo` + `init_repo` (see `tests/test_scheduler.py::_prepare_feature`), then **manually** write one `Issue` + `seed_verification` + `confirm_queue` + an approved adr/prd stub — bypassing Phase A — and construct `IssueRun` directly with `MockBackend(demo_scripts())`. Assert: a passing TDD script → `Outcome("done")` and `store.verification(slug)[id].passes is True`; an always-failing script (`make_tdd_script(fail_first=True)` pattern from `test_scheduler.py:114`) → retries then `Outcome("escalated")`. Factor a `_one_issue_repo(tmp_path)` helper.
- [ ] **Step 5.3 — Run, expect FAIL.**
- [ ] **Step 5.4 — Implement `IssueRun`** by MOVING the body of `_work_issue` (and the worker extension branch) verbatim, swapping `self.` for `self._deps.` / injected callbacks. Keep `try/finally` lock+hook+cancel teardown.
- [ ] **Step 5.5 — Rewrite `scheduler._work_issue`** as: build `SchedulerDeps`, `return (await IssueRun(slug=slug, issue=issue, deps=deps, reviewer_answer=reviewer_answer, janitor_kind=janitor_kind).run()).status`.
- [ ] **Step 5.6 — Run, expect PASS** on `test_issue_run.py`.
- [ ] **Step 5.7 — Full suite green** (this is the big regression check — `test_scheduler.py`, `test_janitor.py`, `test_locks.py`, `test_integration_ws56.py` all exercise this path).
- [ ] **Step 5.8 — Commit:** `refactor(scheduler): extract IssueRun; scheduler becomes a dispatcher (deepening 2)`

**Wins:** locality (retry/lock/extension state in one place); test one run not the whole loop; scheduler shrinks toward a dispatcher.

---

## Cycle 6 — FileStore owns the `.foreman/` layout (deepening 4) · `Strong`

**Problem:** `FileStore.paths` is public; callers build paths and read/write/parse files directly. The layout is knowledge spread across many callers.

**Scope reality (from plan review):** `.paths.` appears at **~100 production sites** across `state.py`, `scheduler.py`, `issue_run.py`, `pipeline.py`, `retro/driver.py`, `retro/metrics.py`, `cli.py`, `demo.py`, `tui/app.py`, `tui/controller.py`, and **~25 test sites** across ~10 files. This is a large but mechanical sweep — budget for it. `retro/metrics.py:246-254` (`load_feature_metrics`) and `retro/driver.py:32` (`_records_for`) **duck-type** on `store.paths.runs_dir(slug)` — their docstring contract must change too.

**Approach:** Add intent-named methods to `FileStore` for every read/write callers do; expose narrow raw-`Path` accessors for the unavoidable cases; then rename `paths` → `_paths`. Do this LAST among core refactors so it sweeps the final (post-IssueRun) shape.

**Category (c) — genuinely-raw-Path needs (keep as narrow accessors, NOT eliminated):** `root` (→ `WorktreeManager`, `git_ops.ensure_excluded`), `daily_cost_file` (→ `CostLedger`; also `demo.py:105`, `controller.py:86`), `feature_dir` / `evidence_dir` / `issue_check_dir` / `baseline_path` / `runs_dir` / `init_script` / `feature_state_file` (→ `RunSpec(repo_root=, cwd=, extra_dirs=)`, subprocess cwd, `run_gate`). The construction at `scheduler.py:117-119` (`WorktreeManager(store.paths.root, ...)`, `CostLedger(store.paths.daily_cost_file)`) uses these accessors.

**Files:**
- Modify: `src/foreman/state.py` (new methods + accessors + make `paths`→`_paths`)
- Modify: `src/foreman/scheduler.py`, `src/foreman/issue_run.py`, `src/foreman/pipeline.py`, `src/foreman/retro/driver.py`, **`src/foreman/retro/metrics.py`**, `src/foreman/cli.py`, `src/foreman/demo.py`, `src/foreman/tui/app.py`, `src/foreman/tui/controller.py`, `src/foreman/verification/merge_gate.py` call sites.
- Modify: tests that poke `store.paths.*` to forge files — switch to `store._paths.*` (explicit test seam).

**New `FileStore` methods (finalised against the live grep at Step 6.1):** e.g.
- `write_verdict(slug, run_id, verdict, final_text)`, `write_audit(slug, run_id, report, final_text)` (move `scheduler._write_verdict/_write_audit` bodies in).
- `append_escalation(slug, issue_id, text)`, `read_escalation(slug, issue_id) -> str`, `escalation_exists(...)`.
- `read_progress(slug, run_id) -> str`, `read_feature_state(slug) -> str`, `write_report(slug, text)`.
- `evidence_dir(slug, run_id) -> Path` (the worker genuinely needs a dir path), `issue_check_dir`, `baseline_path`, `feature_dir`, `root`, `runs_dir` exposed as narrow accessors where a raw `Path` is unavoidable (worktree/subprocess cwd).
- `usage_records(slug) -> list[dict]` (move `feature_cost`/`_records_for`/`metrics.load_feature_metrics` globbing in; update the metrics duck-typed contract to call this).
- `write_review_snapshot(slug, kind, version, body)` / `read_review_snapshot(slug, kind, version) -> str` — **NEW layout concept** currently living in `tui/controller.py:330,394,395` as `reviews_dir(slug)/f"{kind}-v{v}-body.md"`. FileStore must own this `-body.md` review-snapshot path (it is layout knowledge, not a pass-through).

- [ ] **Step 6.1 — Grep every `.paths.` use:** `grep -rn "\.paths\." src | grep -v "src/foreman/paths.py"`. Bucket each into (a) has-an-intent-method-already, (b) needs a new method, (c) genuinely needs a raw Path (cwd/extra_dirs/subprocess).
- [ ] **Step 6.2 — TDD each new method:** add a focused test in `tests/test_state.py` per new method (write→read round-trip), red→green.
- [ ] **Step 6.3 — Implement the new `FileStore` methods**, moving bodies from `scheduler.py`/`retro/driver.py` where applicable.
- [ ] **Step 6.4 — Update all call sites** to the intent methods.
- [ ] **Step 6.5 — Rename `self.paths` → `self._paths`** in `state.py`; for category (c) raw-Path needs, expose narrow accessors (`root`, `feature_dir`, `evidence_dir`, `issue_check_dir`, `baseline_path`, `runs_dir`). Update `Scheduler.__init__`/`WorktreeManager`/`CostLedger` construction (`scheduler.py:117-119`) to use accessors.
- [ ] **Step 6.6 — Fix tests** that used `store.paths.*` to forge files — switch to `store._paths.*` (explicit test seam) or a new `store.doc_file_for_test`-style helper.
- [ ] **Step 6.7 — Full suite green.**
- [ ] **Step 6.8 — Commit:** `refactor(state): FileStore owns the .foreman/ layout; paths goes private (deepening 4)`

**Wins:** locality (layout in one module); callers name intents; frontmatter parsing stops leaking; aligns DECISIONS §1/§4.

---

## Cycle 7 — Controller facade (deepening 6) · `Worth exploring`

**Problem:** `tui/controller.py` exposes `.store`, `.scheduler`, `.config`; screens reach through `controller.store.paths.*` (`app.py:308,362`), `controller.scheduler.kill_issue` (`app.py:251`), and `review.*` (`app.py:120`). Presentation couples to core internals.

**Approach:** Give the controller a complete interface and route screens through it. After Cycle 6 the `store.paths.*` reaches in `app.py` already need replacement → fold that here.

**Files:**
- Modify: `src/foreman/tui/controller.py` (add methods: `kill_worker(issue_id) -> bool`, `escalation_text(slug, issue_id) -> str`, `review_digest(slug, kind) -> str`, `config_path() -> Path`), and any others surfaced by the grep.
- Modify: `src/foreman/tui/app.py` (call controller methods; drop `from .. import review` and direct `controller.store`/`controller.scheduler` reaches).
- Modify: `src/foreman/cli.py` retro/bench paths that reach into `controller.store/config` → add controller methods or use existing ones.

- [ ] **Step 7.1 — Grep coupling:** `grep -rn "controller\.\(store\|scheduler\|config\)\b\|from \.\. import review" src/foreman/tui src/foreman/cli.py`.
- [ ] **Step 7.2 — TDD controller methods** in `tests/test_controller.py` (headless): `kill_worker` delegates to scheduler, `escalation_text` reads via store, `review_digest` calls `review.decisions_digest`.
- [ ] **Step 7.3 — Implement controller methods; red→green.**
- [ ] **Step 7.4 — Update `app.py`/`cli.py`** to use them; make `store`/`scheduler`/`config` "internal" (prefix `_` only if no remaining external reads — keep public if tests rely on them, but route screens through methods).
- [ ] **Step 7.5 — Full suite green** (`test_tui.py`, `test_controller.py`).
- [ ] **Step 7.6 — Commit:** `refactor(tui): controller becomes a facade; screens cross one seam (deepening 6)`

**Wins:** presentation crosses one seam; core refactors stop hitting the TUI.

---

## Cycle 8 — StreamEvent vocabulary (deepening 8) · `Speculative`

**Problem:** CLI-shaped `StreamEvent` types cross the `AgentBackend` seam intact; the controller `isinstance`-checks `AssistantMessage`/`ResultEvent` and calls `humanize` in **two** methods — `_on_phase_event` (`controller.py:157,159`) and `worker_event` (`controller.py:195,197,199`) — and `runner.py:49-59` hard-codes Claude tool names (`_PROGRESS_TOOLS`) for stuck-detection.

**Approach (minimal, conservative — this is Speculative):** Do NOT restructure the event hierarchy. Instead remove the two concrete leaks:
1. Move the progress-tool knowledge behind a named predicate in `stream_parser.py` (e.g. `event_made_progress(event) -> bool`) so `runner._made_progress` (`runner.py:49-59`) and any caller use one definition; keep the `_PROGRESS_TOOLS` set in `stream_parser.py` only.
2. Add `StreamEvent.is_assistant` (base `:21` → False; override in `AssistantMessage` `:75` → True) so the controller increments turns via `event.is_assistant` at BOTH sites rather than importing the concrete class. `ResultEvent` (cost, `controller.py:197`) is also isinstance-checked — add `is_result` likewise, or leave it (it is lower-value); document the choice. The backend still yields the same objects.

**Files:** `src/foreman/stream_parser.py`, `src/foreman/runner.py`, `src/foreman/tui/controller.py`, `tests/test_stream_parser.py`.

- [ ] **Step 8.1 — Read live `stream_parser.py`, `runner.py:49-59`, `controller.py` `_on_phase_event` + `worker_event`.**
- [ ] **Step 8.2 — TDD `event_made_progress` + `is_assistant`** in `tests/test_stream_parser.py`.
- [ ] **Step 8.3 — Implement; move the `_PROGRESS_TOOLS` set into `stream_parser.py`.**
- [ ] **Step 8.4 — Update `runner.py` and `controller.py`** to use the predicates; drop the concrete-type imports in the controller where possible.
- [ ] **Step 8.5 — Full suite green.**
- [ ] **Step 8.6 — Commit:** `refactor(stream): keep CLI tool-name + event-kind knowledge behind the seam (deepening 8)`

**Wins:** the seam hides CLI specifics; tool names in one place; TUI survives stream-schema drift.

---

## Final review (after all cycles)

- [ ] Run the full suite once more; confirm 290+ green and no skips introduced.
- [ ] `git log --oneline` — confirm one clean commit per deepening.
- [ ] Re-read each new module's interface against the review's "After" diagrams.
- [ ] Update `DECISIONS.md` with a short "Architecture deepening (2026-06-15)" note listing the new seams (`seal`, `prompts`, `merge_gate`, `issue_run`, FileStore-owns-layout) — so future reviews don't re-suggest them.
- [ ] Bump version + CHANGELOG entry (`0.5.0` — internal architecture deepening, no behaviour change) only at the very end.
- [ ] `superpowers:finishing-a-development-branch` to decide merge/PR.

## Self-review notes (spec coverage)

All 8 review candidates are covered: 5→C1, 3→C2, 7→C3, 1→C4, 2→C5, 4→C6, 6→C7, 8→C8. No candidate dropped. Order chosen so shared files (`scheduler.py`, `state.py`) are swept by the *last* relevant cycle (IssueRun before FileStore-layout; FileStore-layout before controller facade). Behaviour-preserving throughout; the 290-test suite is the safety net and every cycle ends on green.
