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
foreman status            # show vendored-skill status + features for the repo
foreman init --force      # re-create config and reinstall the foreman-* skills
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

For each ready issue (queued + dependencies done), up to `max_parallel` workers run
concurrently, each in its **own git worktree**:

- A `foreman-tdd` worker implements the slice with strict red-green-refactor and
  emits a machine-readable `FOREMAN-SUMMARY` block.
- **Foreman re-runs the configured `test`/`lint`/`typecheck` commands itself** and
  blocks "done" on failure — it never trusts the agent's claim.
- Pass → commit + merge into the integration branch. Fail → retry with the failing
  output appended, up to `max_retries`, then **escalate** to the attention queue.
- Budget breaches (turns / cost / wall-clock), stuck workers, or an agent's own
  escalation request all route to the attention queue, where you answer and the
  worker **resumes**.
- When all issues land and the PRD defines user flows, an **e2e** agent derives and
  runs end-to-end tests (same independent verification).

### Guardrails (enforced by Foreman, not the agent)

Per-run `max_turns`, `max_cost_usd`, `timeout_min`; per-issue `max_retries`;
global `max_parallel` and a daily cost ceiling with a hard stop. Cost is taken
from the stream's `total_cost_usd`; the native `--max-budget-usd` flag is passed as
a second line of defence. Every enforcement event is logged and surfaced.

---

## File layout (created inside your repo by `foreman init`)

```
.foreman/
  config.yaml
  daily_cost.json
  features/<slug>/
    request.md  plan.md  adr.md  prd.md  report.md
    reviews/    plan-v1-review.md ...
    issues/     ISS-001.md ...
    escalations/ISS-001.md ...
    runs/<timestamp>-ISS-001/{transcript.jsonl, summary.md, usage.json}
.claude/skills/
  foreman-grill-docs/  foreman-to-prd/  foreman-to-issues/  foreman-tdd/
```

Document statuses: `drafting → in_review → changes_requested → approved`
(approval auto-reverts if the body changes). Issue statuses:
`queued | in_progress | tests_failing | needs_human | done | merged`.

## Configuration (`.foreman/config.yaml`)

See `config.sample.yaml` for the annotated template. Key fields: `model_planner`,
`model_worker`, `effort`, `required_skills`, `commands` (test/lint/typecheck/e2e),
`git` (integration branch, merge strategy, open_pr), `limits` (max_parallel,
max_retries, daily_cost_usd), `run_budget`, `e2e_enabled`, `permission_mode`.

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
