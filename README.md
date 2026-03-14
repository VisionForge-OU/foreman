# Foreman

A Boris-style **agentic orchestrator TUI** that supervises headless
[Claude Code](https://claude.com/claude-code) agents through a *gated*
software-delivery pipeline — **plan → ADR/PRD → issues → TDD build → e2e** —
pointed at any target repository.

> "I don't prompt Claude anymore; I have loops that prompt Claude."

Foreman spawns the locally-installed `claude` CLI in headless stream-json mode,
parses its event stream, enforces budgets, and drives your delivery workflow with
a human-in-the-loop review gate for the design phases and guardrailed autonomy for
the build. **All state is human-readable files committed inside the target repo** —
no database; kill it and restart and it fully recovers from disk.

---

## 5-minute quickstart

```bash
# 1. Install (exposes a single `foreman` command)
pipx install .            # or:  uv tool install .

# 2. Point it at any repo
cd /path/to/your/repo
foreman init              # scaffolds .foreman/ and installs the foreman-* skills
                          #   into .claude/skills/

# 3. See the whole thing work end-to-end with NO tokens spent
foreman demo              # runs the full pipeline against a throwaway sample repo
                          #   using a mocked agent backend (canned stream-json)

# 4. Launch the TUI for real work
foreman                   # (same as `foreman tui`)
foreman --demo            # launch the TUI against a throwaway sample repo
```

Other commands:

```bash
foreman status            # show vendored-skill + agent status + features for the repo
foreman init --force      # re-create config and reinstall the foreman-* skills/agents
foreman build             # resume/continue the autonomous build of a feature
foreman retro             # cluster recurring failures → gated skill/prompt patch drafts
foreman bench             # replay the eval set; report success-rate/cost/turn deltas
foreman --version
```

### Requirements

- Python 3.11+
- The `claude` CLI installed and authenticated (`claude --version`)
- `git`
- Linux / WSL2 (developed and tested on Ubuntu under WSL2)

---

## How it works

### Phase A — the gated pipeline (human in the loop)

1. **Create a feature** in the TUI (title + description + product requirements) →
   `request.md`.
2. **Plan** — a high-reasoning planner agent (`--effort` from config) turns the
   request into a deep implementation plan → `plan.md` (status `in_review`).
3. **Grill** — the vendored `foreman-grill-docs` skill challenges the *approved*
   plan against the codebase and domain model and writes an **ADR** and a **PRD**.
   Because there is no live user, it self-answers everything it can and surfaces
   the rest under an **"Open questions for reviewer"** block.
4. **Review** — you review each draft in the TUI: **a** approve, **r** request
   changes (your comments answer the open questions), **tab** to switch docs.
   A draft with open questions **cannot** be approved. Editing an approved doc
   automatically invalidates its approval (a SHA-256 of the body is checked on
   every load).
5. **Slice** — once the PRD is approved, `foreman-to-issues` breaks it into small,
   dependency-ordered, vertically-sliced issue files with PRD traceability.
6. **Confirm the queue** — the final gate. Nothing downstream runs until you
   confirm.

### Phase B — the autonomous build loop ("Boris loop")

A one-time **initializer** writes `init.sh` + `feature-state.md`. Then, for each
ready issue (queued + dependencies done) whose declared **`touches`** footprint
doesn't overlap a running one, up to `max_parallel` workers run concurrently, each
in its **own git worktree**:

- A `foreman-tdd` worker implements the slice (red-green-refactor), runs tests via
  the installed **`foreman-test`** wrapper, saves **evidence** under
  `runs/<id>/evidence/`, updates **`progress.md`**, and emits a `FOREMAN-SUMMARY`.
- The **merge gate** (Foreman runs it itself, never trusting the agent) requires:
  the worker's **evidence** is real, the issue's runnable **`acceptance_check`**
  passes, the full **test/lint/typecheck** pass, and the **regression ratchet** is
  green (no previously-passing test now fails — bounces name the regressed tests).
- A read-only **evaluator** (a separate `--agent`, fresh context) then grades the
  diff on a 1–5 rubric; objections **bounce to a fresh worker** (with a distilled
  failure report), uncertainty/repeated disagreement **escalate**.
- Pass → Foreman flips the issue's entry in `verification.json` (workers are
  **hook-blocked** from writing it), commits + merges. After every N merges a
  **janitor** pass (dedup / conventions / docs) runs through the same gate.
- When all issues land, an **e2e** agent runs, then a read-only **auditor** walks
  the PRD requirement-by-requirement; a divergence drafts a **PRD amendment** that
  re-enters the hash-sealed review gate.

### Guardrails (enforced by Foreman, not the agent)

Per-run `max_turns`, `max_cost_usd`, `timeout_min`; per-issue `max_retries`;
global `max_parallel` and a daily cost ceiling with a hard stop. Workers can't
write `verification.json` / issue files (a `PreToolUse` deny hook, proven to hold
under `acceptEdits`); crash-safe per-issue **locks** with heartbeat reclaim prevent
double-claiming. Every enforcement event is logged and surfaced.

### The evals flywheel

Every run is **outcome-labelled** (`success_first_try | success_after_retry(n) |
evaluator_bounce | escalated(reason) | …`); the TUI metrics pane (press **m**)
renders success rate, mean retries/issue, cost/issue, and an escalation histogram.
`foreman retro` clusters recurring failures and drafts patches to the vendored
skills / rubric / prompts — drafts that pass the **same hash-sealed review gate** as
a PRD; **no patch lands without a `foreman bench` report** showing it doesn't
regress the eval set.

---

## File layout (created inside your repo by `foreman init`)

```
.foreman/
  schema_version            # on-disk schema version (2); Phase-1 trees migrate additively
  config.yaml   daily_cost.json   SKILL_CHANGELOG.md
  retro/                    # gated skill/prompt patch proposals + bench reports
  features/<slug>/
    request.md  plan.md  adr.md  prd.md  report.md
    feature-state.md  init.sh             # initializer outputs (WS3)
    verification.json  baseline.json      # Foreman-owned structural-done + ratchet baseline
    reviews/    plan-v1-review.md  prd-v1-body.md ...
    issues/     ISS-001.md  ISS-001.check/ ...   # each issue ships a runnable acceptance check
    escalations/ISS-001.md ...
    runs/<timestamp>-ISS-001/{transcript.jsonl, summary.md, usage.json,
                              progress.md, verdict.json, evidence/}
.claude/skills/   foreman-grill-docs/  foreman-to-prd/  foreman-to-issues/  foreman-tdd/
.claude/agents/   foreman-evaluator.md  foreman-auditor.md  foreman-retro.md
```

Document statuses: `drafting → in_review → changes_requested → approved`
(approval auto-reverts if the body changes). Issue statuses:
`queued | in_progress | tests_failing | awaiting_evaluation | needs_human | done | merged`.

## Configuration (`.foreman/config.yaml`)

See `config.sample.yaml` for the annotated template. Key fields: `model_planner`,
`model_worker`, `model_evaluator`, `model_auditor`, `effort`, `required_skills`,
`required_agents`, `commands` (test/lint/typecheck/e2e), `git`, `limits`,
`run_budget`, `evaluator_*`, `auditor_enabled`, `notify_command`, `retry_strategy`
(`fresh`|`resume`), `janitor_enabled`/`janitor_every`/`janitor_kinds`,
`bench_eval_set`/`bench_cost_ceiling_usd`, `e2e_enabled`, `permission_mode`.

## The vendored skills

Foreman ships forked, namespaced copies of four
[mattpocock/skills](https://github.com/mattpocock/skills) — `foreman-grill-docs`,
`foreman-to-prd`, `foreman-to-issues`, `foreman-tdd` — rewritten for headless,
non-interactive orchestration with a local (non-GitHub) issue layer. The pipeline
references **only** these names, so it never resolves to a user-installed upstream
copy; your other installed skills remain available to workers. See `NOTICE` for
attribution and `DECISIONS.md` §8 for the per-skill changelog.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
pytest -q                 # full suite (uses mocked agents + real git/pytest)
```

The whole system is exercised offline via a mocked agent backend
(`foreman demo` / the test suite) so the state machine and TUI are testable without
burning tokens. The single seam is `AgentBackend` (`backend.py`): `ClaudeBackend`
spawns the real CLI; `MockBackend` replays canned stream-json.

## License

MIT. Portions derived from mattpocock/skills (MIT) — see `LICENSE` and `NOTICE`.
