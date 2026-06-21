<div align="center">

# Foreman

**A Boris-style agentic orchestrator TUI that supervises headless [Claude Code](https://claude.com/claude-code) agents through a _gated_ software-delivery pipeline — pointed at any repository.**

`plan → ADR/PRD → issues → TDD build → e2e`

[![CI](https://github.com/VisionForge-OU/foreman/actions/workflows/ci.yml/badge.svg)](https://github.com/VisionForge-OU/foreman/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/VisionForge-OU/foreman/branch/main/graph/badge.svg)](https://codecov.io/gh/VisionForge-OU/foreman)
[![PyPI version](https://img.shields.io/pypi/v/foreman-orchestrator.svg)](https://pypi.org/project/foreman-orchestrator/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/foreman-orchestrator.svg)](https://pypi.org/project/foreman-orchestrator/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/foreman-orchestrator.svg)](https://pypi.org/project/foreman-orchestrator/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![TUI: Textual](https://img.shields.io/badge/TUI-Textual-5a3fd6.svg)](https://textual.textualize.io/)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

[Why Foreman?](#why-foreman) · [Demo](#demo) · [Quickstart](#5-minute-quickstart) · [Guide](#guide--driving-the-tui) · [How it works](#how-it-works) · [Roadmap](#roadmap) · [Contributing](#contributing)

</div>

> _"I don't prompt Claude anymore; I have loops that prompt Claude."_

Foreman spawns the locally-installed `claude` CLI in headless stream-json mode,
parses its event stream, enforces budgets, and drives your delivery workflow with
a human-in-the-loop review gate for the design phases and guardrailed autonomy for
the build. **All state is human-readable files committed inside the target repo** —
no database; kill it and restart and it fully recovers from disk.

---

## Table of contents

- [Why Foreman?](#why-foreman)
- [Demo](#demo)
- [5-minute quickstart](#5-minute-quickstart)
- [Guide — driving the TUI](#guide--driving-the-tui)
- [How it works](#how-it-works)
- [Roadmap](#roadmap)
- [File layout](#file-layout)
- [Configuration](#configuration-foremanconfigyaml)
- [The vendored skills](#the-vendored-skills)
- [Development](#development)
- [Contributing](#contributing)
- [FAQ](#faq)
- [Acknowledgements](#acknowledgements)
- [License](#license)

---

## Why Foreman?

Running a coding agent in a `while` loop is easy. Running one you can **trust to
merge** is not. Foreman is the supervisor in between: it keeps a human at the design
gates, then hands the build to agents that are **boxed in by the orchestrator, not
by their own good behaviour**.

- 🚦 **Gated pipeline** — `plan → ADR/PRD → issues → TDD build → e2e`, with human review gates on every design phase and a hash-sealed approval that auto-reverts if a doc changes.
- 🤖 **Real headless agents** — spawns the locally-installed `claude` CLI in stream-json mode, parses its events, and enforces per-run turn/cost/time budgets.
- 💾 **No database** — all state is human-readable files committed inside the target repo. Crash-safe: kill it mid-build and it recovers from disk.
- ⌨️ **Keyboard-driven TUI** — drive the entire workflow from a Textual terminal UI ([full keymap below](#guide--driving-the-tui)).
- 🧰 **Worktree isolation** — parallel workers each run in their own git worktree, footprint-gated by a declared `touches` set so they never collide.
- 🛡️ **Guardrails Foreman enforces (not the agent)** — per-run caps, a daily cost ceiling with a hard stop, and a `PreToolUse` deny hook that blocks workers from writing their own verification.
- 🔁 **Evals flywheel** — every run is outcome-labelled; `foreman retro` clusters failures into gated skill/prompt patches that must pass `foreman bench` before they can land.

---

## Demo

```bash
foreman --demo        # launch the full TUI against a throwaway sample repo,
                      # driven by a mocked agent backend — ZERO tokens spent
```

`foreman demo` (non-interactive) and `foreman --demo` (the live TUI) run the entire
`plan → … → e2e` pipeline on canned stream-json, so you can explore every gate and
screen before spending a cent.

<!-- Tip: drop a real terminal recording or screenshot here, e.g. an asciinema cast or a docs/demo.gif -->

Dashboard at a glance _(illustrative layout — run `foreman --demo` to see it live)_:

```
┌ Foreman ─────────────────────────────────── agentic delivery orchestrator ──┐
│ Features (n)            │ daily-plan — phase: building   cost: $0.41   ●2 wk │
│ ▸ daily-plan            │ Press b to (re)start · w workers · x attention     │
│   backlog-aging         │                                                    │
│                         │  Issue board                                       │
│ Vendored skills         │  queued     in_progress   done       merged        │
│  ✓ foreman-tdd     v4   │  ISS-004    ISS-002       ISS-001    ISS-003       │
│  ✓ foreman-grill…  v3   │             ISS-005                                │
│ Read-only agents        │                                                    │
│  ✓ foreman-evaluator    │  [ global activity log … ]                         │
├─────────────────────────┴────────────────────────────────────────────────┤
│ ⠹ ACTIVE  ISS-002 worker · turn 12/30 · $0.18 · running pytest             │
│ n New  p Plan  g Grill  s Slice  c Confirm  b Build  v Review  w Workers …  │
└────────────────────────────────────────────────────────────────────────────┘
```

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

<details>
<summary><b>Other CLI commands</b></summary>

```bash
foreman status            # show vendored-skill + agent status + features for the repo
foreman init --force      # re-create config and reinstall the foreman-* skills/agents
foreman build             # resume/continue the autonomous build of a feature
foreman retro             # cluster recurring failures → gated skill/prompt patch drafts
foreman bench             # replay the eval set; report success-rate/cost/turn deltas
foreman --version
```

</details>

### Requirements

- Python 3.11+
- The `claude` CLI installed and authenticated (`claude --version`)
- `git`
- Linux / WSL2 (developed and tested on Ubuntu under WSL2)

---

## Guide — driving the TUI

Foreman is **fully keyboard-driven**. Launch it with `foreman` (or `foreman --demo`
to try it with a mocked backend and zero token spend). Every screen also shows its
keys in the footer; the reference below is the complete map.

<details>
<summary><b>📖 Click to expand the full TUI guide</b></summary>

### The shape of a session

You spend almost all of your time on the **Dashboard**. It lists your features on
the left, shows the selected feature's current phase + cost + a live issue board on
the right, and tells you the single next key to press in its hint line. The other
screens (Review, Workers, Attention, Metrics, Retro, Settings) are pushed on top
with a single key and dismissed with <kbd>Esc</kbd>.

A feature moves through phases; the Dashboard hint tells you what to press at each:

| Phase | Hint shown | You press |
|-------|------------|-----------|
| `request` | Run the planner | <kbd>p</kbd> |
| `plan_review` | Review the plan (a=approve, r=request changes) | <kbd>v</kbd> |
| `grilling` | Run the grill (ADR + PRD) | <kbd>g</kbd> |
| `doc_review` | Review ADR / PRD | <kbd>v</kbd> |
| `slicing` | Run the slicer | <kbd>s</kbd> |
| `queue_review` | Confirm the queue, then build | <kbd>c</kbd> then <kbd>b</kbd> |
| `building` | (Re)start the build · workers · attention | <kbd>b</kbd> · <kbd>w</kbd> · <kbd>x</kbd> |
| `done` | Feature complete 🎉 — see `report.md` | — |

### Dashboard — global keys

The home screen. Select a feature with the arrow keys, then act on it.

| Key | Action |
|-----|--------|
| <kbd>↑</kbd> / <kbd>↓</kbd> | Select a feature in the list |
| <kbd>n</kbd> | **New** feature (opens the create modal) |
| <kbd>p</kbd> | Run the **planner** → `plan.md` |
| <kbd>g</kbd> | Run the **grill** → ADR + PRD |
| <kbd>s</kbd> | Run the **slicer** → issue files |
| <kbd>c</kbd> | **Confirm** the queue (final gate before build) |
| <kbd>b</kbd> | Start / resume the **build** loop |
| <kbd>v</kbd> | Open the **Review** screen (plan / ADR / PRD) |
| <kbd>w</kbd> | Open the **Worker** view (live agent logs) |
| <kbd>x</kbd> | Open the **Attention** queue (escalations) |
| <kbd>m</kbd> | Open the **Metrics** pane |
| <kbd>t</kbd> | Open the **Retro** patch gate |
| <kbd>,</kbd> | Open **Settings** (read-only config view) |
| <kbd>q</kbd> | Quit |

### New-feature modal (<kbd>n</kbd>)

A small form: type a **title**, <kbd>Tab</kbd> into the **request** box (description
+ product requirements), then click **Create** (or **Cancel**). Submitting writes
`request.md` and selects the new feature.

### Review screen (<kbd>v</kbd>) — the design gate

Where you approve or push back on the `plan`, `adr`, and `prd` drafts. The top of
the screen surfaces the grill's **"decisions made on your behalf"** digest and any
**open questions**; the body renders the document; a comment box at the bottom is
used as your **answers** to those open questions.

| Key | Action |
|-----|--------|
| <kbd>a</kbd> | **Approve** the current doc |
| <kbd>r</kbd> | **Request changes** — uses the comment box as answers / change requests |
| <kbd>Tab</kbd> | Cycle to the next doc (`plan` → `adr` → `prd`) |
| <kbd>Esc</kbd> | Back to the dashboard |

- A draft with **open questions cannot be approved** — answer them via a
  request-changes comment first, then re-run the grill/planner to revise.
- **Approval is hash-sealed**: editing an approved doc's body auto-invalidates its
  approval (a SHA-256 of the body is re-checked on every load).
- Requesting changes on a PRD **amendment** can spin off concrete fix issues — the
  notification tells you how many and to press <kbd>b</kbd> to build them.

### Worker view (<kbd>w</kbd>) — watch the build

A sidebar of running workers (`id [status] $cost turns`) and a live, scrolling log
of the selected worker's raw agent output, with a budget bar on top.

| Key | Action |
|-----|--------|
| <kbd>↑</kbd> / <kbd>↓</kbd> | Select a worker (log follows the highlight) |
| <kbd>Tab</kbd> | Jump to the next worker |
| <kbd>k</kbd> | **Kill** the selected worker |
| <kbd>Esc</kbd> | Back to the dashboard |

### Attention queue (<kbd>x</kbd>) — rescue escalations

When a worker escalates (uncertainty, repeated evaluator disagreement, …) it lands
here and the terminal bells. Select an escalation, read its detail, type your answer,
and resume the worker — which picks up your answer in a fresh context.

| Key | Action |
|-----|--------|
| <kbd>↑</kbd> / <kbd>↓</kbd> | Select an escalation |
| <kbd>Ctrl</kbd>+<kbd>N</kbd> | Next escalation |
| <kbd>Enter</kbd> | Newline **inside** the answer box (does _not_ submit) |
| <kbd>Ctrl</kbd>+<kbd>S</kbd> | **Submit your answer & resume** the worker |
| <kbd>Esc</kbd> | Back to the dashboard |

> Submit is <kbd>Ctrl</kbd>+<kbd>S</kbd>, not <kbd>Enter</kbd>, so <kbd>Enter</kbd> stays free for
> multi-line answers. The submit binding fires even while the answer box has focus.

### Metrics pane (<kbd>m</kbd>)

Success rate, mean retries/issue, cost/issue, an escalation histogram, and trends
across runs for the selected feature. <kbd>Esc</kbd> returns to the dashboard.

### Retro patch gate (<kbd>t</kbd>) — the human side of the flywheel

Lists the gated skill/prompt patch proposals in `.foreman/retro/`. Select one to see
its diff + rationale + attached bench delta. **A patch lands only with both your
approval and a `foreman bench` report** — the gate is enforced here.

| Key | Action |
|-----|--------|
| <kbd>↑</kbd> / <kbd>↓</kbd> | Select a proposal |
| <kbd>Tab</kbd> | Next proposal |
| <kbd>a</kbd> | **Approve** the proposal |
| <kbd>r</kbd> | **Reject** the proposal |
| <kbd>l</kbd> | **Land** it (requires approval **and** a bench report) |
| <kbd>Esc</kbd> | Back to the dashboard |

> Generating proposals (`foreman retro`) and benchmarking them (`foreman bench`) are
> long, token-spending agent runs and stay on the CLI; only the review/approve/
> reject/land gate lives in the TUI.

### Settings (<kbd>,</kbd>)

A read-only render of the active configuration. Edit `.foreman/config.yaml`
directly — it is validated on load. <kbd>Esc</kbd> returns to the dashboard.

### Cheat-sheet

| Screen | Keys |
|--------|------|
| **Dashboard** | <kbd>n</kbd> new · <kbd>p</kbd> plan · <kbd>g</kbd> grill · <kbd>s</kbd> slice · <kbd>c</kbd> confirm · <kbd>b</kbd> build · <kbd>v</kbd> review · <kbd>w</kbd> workers · <kbd>x</kbd> attention · <kbd>m</kbd> metrics · <kbd>t</kbd> retro · <kbd>,</kbd> settings · <kbd>q</kbd> quit |
| **Review** | <kbd>a</kbd> approve · <kbd>r</kbd> request changes · <kbd>Tab</kbd> next doc · <kbd>Esc</kbd> back |
| **Workers** | <kbd>k</kbd> kill · <kbd>Tab</kbd> next · <kbd>↑</kbd>/<kbd>↓</kbd> select · <kbd>Esc</kbd> back |
| **Attention** | <kbd>Ctrl</kbd>+<kbd>S</kbd> answer & resume · <kbd>Ctrl</kbd>+<kbd>N</kbd> next · <kbd>Esc</kbd> back |
| **Retro** | <kbd>a</kbd> approve · <kbd>r</kbd> reject · <kbd>l</kbd> land · <kbd>Tab</kbd> next · <kbd>Esc</kbd> back |
| **Metrics / Settings** | <kbd>Esc</kbd> back |

</details>

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
5. **Slice** — once the ADR and PRD are both approved, `foreman-to-issues` breaks
   the PRD into small, dependency-ordered, vertically-sliced issue files with PRD
   traceability.
6. **Confirm the queue** — the final gate. The queue view shows each issue's
   runnable `acceptance_check`, `touches`, `prd_refs`, dependencies, and conflict
   graph. Nothing downstream runs until you confirm.

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
  failure report), uncertainty/repeated disagreement **escalate**. Two further
  opt-in read-only graders — **code-review** and **security-review** — can run on
  the committed slice and bounce/escalate on a blocking verdict.
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

## Roadmap

Foreman ships **Phase 1 + Phase 2** today. Phase 3 is planned, not yet implemented.

- ✅ **Phase 1 — the gated pipeline.** `plan → ADR/PRD → issues → TDD → e2e`,
  worktree-isolated parallel build, Foreman-owned merge gate + regression ratchet,
  read-only evaluator/auditor, crash-safe file state, and the full Textual TUI.
- ✅ **Phase 2 — the evals flywheel.** Run outcome taxonomy, the metrics pane,
  `foreman retro` / `foreman bench`, and hash-sealed skill/prompt patch landing.
  _(0.6.0 adds opt-in code-review & security-review gate agents — see [`CHANGELOG.md`](./CHANGELOG.md).)_
- 🚧 **Phase 3 — hardening (planned).** Training-data exporter, worker sandboxing,
  CLI-contract probes, and a chaos suite.

---

## File layout

<details>
<summary>What <code>foreman init</code> creates inside your repo</summary>

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

</details>

## Configuration (`.foreman/config.yaml`)

See `config.sample.yaml` for the annotated template. Key fields: `model_planner`,
`model_worker`, `model_evaluator`, `model_auditor`, `effort`, `required_skills`,
`required_agents`, `commands` (test/lint/typecheck/e2e), `git`, `limits`,
`run_budget`, `evaluator_*`, `auditor_enabled`, `notify_command`, `retry_strategy`
(`fresh`|`resume`), `janitor_enabled`/`janitor_every`/`janitor_kinds`,
`bench_eval_set`/`bench_cost_ceiling_usd`, `e2e_enabled`, `permission_mode`.

## The vendored skills

Foreman ships forked, namespaced copies of skills from
[mattpocock/skills](https://github.com/mattpocock/skills),
[obra/superpowers](https://github.com/obra/superpowers), and Anthropic — e.g.
`foreman-grill-docs`, `foreman-to-prd`, `foreman-to-issues`, `foreman-tdd`,
`foreman-debug`, `foreman-verify` — rewritten for headless, non-interactive
orchestration with a local (non-GitHub) issue layer. The pipeline references
**only** these names, so it never resolves to a user-installed upstream copy; your
other installed skills remain available to workers. See [`NOTICE`](./NOTICE) for
attribution and [`DECISIONS.md`](./DECISIONS.md) §8 for the per-skill changelog.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
pytest -q                 # full suite (uses mocked agents + real git/pytest)
```

The whole system is exercised offline via a mocked agent backend
(`foreman demo` / the test suite) so the state machine and TUI are testable without
burning tokens. The single seam is `AgentBackend` (`backend.py`): `ClaudeBackend`
spawns the real CLI; `MockBackend` replays canned stream-json.

Design rationale lives in [`DECISIONS.md`](./DECISIONS.md); release history in
[`CHANGELOG.md`](./CHANGELOG.md).

## Contributing

Contributions are welcome. Foreman is developed in the open at
[`n1arash/foreman`](https://github.com/n1arash/foreman).

1. **Open an issue** for a bug or proposal:
   [github.com/n1arash/foreman/issues](https://github.com/n1arash/foreman/issues).
2. **Set up the dev env** (see [Development](#development)) and branch off `main`.
3. **Keep the suite green** — `pytest -q` runs fully offline on the mocked backend,
   so no tokens are spent in CI or locally. Add tests for new behaviour.
4. **Follow the conventions** — state lives in human-readable files; the only seam
   to the real agent is `AgentBackend`; gates are enforced by Foreman, never trusted
   to the agent. New cross-cutting decisions get an entry in [`DECISIONS.md`](./DECISIONS.md).
5. **Open a PR** against `main` with a clear description and a `CHANGELOG.md` note.

## FAQ

<details>
<summary><b>Does trying it cost tokens?</b></summary>

No. `foreman demo` and `foreman --demo` run the entire pipeline on a **mocked**
agent backend (canned stream-json), so you can explore every gate and screen with
zero token spend. Only real feature work against your repo spawns the `claude` CLI.

</details>

<details>
<summary><b>Do I need a database or a server?</b></summary>

No. Every bit of state is a human-readable file committed inside your target repo
under `.foreman/`. There is no daemon and no database — kill Foreman mid-build and
restart, and it recovers from disk.

</details>

<details>
<summary><b>Which models does it use?</b></summary>

Whatever you configure per role in `.foreman/config.yaml` — `model_planner`,
`model_worker`, `model_evaluator`, `model_auditor` — plus an `effort` knob for the
planner. Different stages can run different models.

</details>

<details>
<summary><b>Is my repo safe from a runaway agent?</b></summary>

Workers run in **isolated git worktrees** under per-run turn/cost/time caps and a
daily cost ceiling with a hard stop, and are **hook-blocked** from writing their own
verification. For unsupervised runs, use the strictest `permission_mode` (and ideally
a container). Foreman never trusts the agent's self-report — it re-runs the gates.

</details>

<details>
<summary><b>Does it work outside Linux?</b></summary>

It's developed and tested on Linux / WSL2 (Ubuntu). It should run anywhere the
`claude` CLI, `git`, and Python 3.11+ are available, but Linux/WSL2 is the tested path.

</details>

## Star History

[![Star History Chart](https://api.star-history.com/chart?repos=VisionForge-OU/foreman&type=date&legend=top-left)](https://www.star-history.com/?repos=VisionForge-OU%2Fforeman&type=date&legend=top-left)

## Acknowledgements

- Built on [Textual](https://textual.textualize.io/) for the TUI and the
  [Claude Code](https://claude.com/claude-code) CLI for the agents.
- Vendored, headless-rewritten skills are forked from
  [mattpocock/skills](https://github.com/mattpocock/skills) (MIT),
  [obra/superpowers](https://github.com/obra/superpowers) (MIT), and Anthropic's
  skills — see [`NOTICE`](./NOTICE) for full attribution.

## License

[MIT](./LICENSE). Portions derived from [mattpocock/skills](https://github.com/mattpocock/skills)
and others (MIT) — see [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE) for attribution.
