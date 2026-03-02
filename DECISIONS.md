# DECISIONS.md — Foreman architecture & rationale

Foreman is a Textual TUI that orchestrates headless Claude Code agents in supervised
loops to drive a gated software-delivery pipeline (plan → ADR/PRD → issues → TDD build → e2e),
pointed at any target repo. This file records every non-obvious decision and assumption.

---

## 0. Empirical verification of the Claude Code interface (per R1 / §11.2)

Verified against the locally installed `claude` CLI **v2.1.174** on 2026-06-12.

### Flags we rely on (confirmed present in `claude --help`)
- `-p, --print` — non-interactive; prints and exits. Required for headless workers.
- `--output-format stream-json` — newline-delimited JSON event stream (verified live).
- `--verbose` — **required** alongside `stream-json` to get the full event stream.
- `--model <alias|full>` — e.g. `claude-fable-5`, `fable`, `opus`. Confirmed.
- `--effort <level>` — choices: `low, medium, high, xhigh, max`. This is the documented
  mechanism for requesting high reasoning effort in headless mode (§6.2). We map
  `config.effort` straight onto it.
- `--permission-mode <mode>` — choices: `acceptEdits, auto, bypassPermissions, default,
  dontAsk, plan`. We default to `acceptEdits` (config-driven, never silently bypass).
- `--max-budget-usd <amount>` — native per-invocation cost ceiling (only with `--print`).
  We pass this AND enforce our own ceiling by parsing usage, belt-and-suspenders (R5/R9).
- `--add-dir <dirs...>` — extra allowed dirs (used to grant worktree + feature dir access).
- `--resume [sessionId]` / `--session-id <uuid>` — session resume for escalation answers (§7).
- `--settings <file-or-json>` — load extra settings (we DON'T strip the user's normal
  settings; workers run with the user's environment, cwd in the target repo, per R2).
- `--strict-mcp-config` / `--mcp-config` — NOT used; we deliberately keep the user's MCP
  + skills available to workers (R2: "Other user-installed skills must remain available").

### Flags that DO NOT exist (so Foreman must enforce them itself)
- There is **no `--max-turns` flag**. Therefore Foreman enforces the per-run turn budget
  itself by counting assistant turns in the stream and killing the subprocess when the
  ceiling is hit (R5: "enforced by Foreman ... not by trusting the agent"). Same for the
  wall-clock timeout (asyncio timeout → kill).

### stream-json event schema (captured live, see `fixtures/`)
A `claude -p ... --output-format stream-json --verbose` run emits NDJSON lines. Observed types:
- `{"type":"system","subtype":"init", ...}` — session_id, cwd, model, tools, skills,
  slash_commands, permissionMode. We use this to confirm the session started and capture
  `session_id` (needed for `--resume`).
- `{"type":"system","subtype":"hook_started"|"hook_response", ...}` — local hooks firing.
- `{"type":"system","subtype":"thinking_tokens","estimated_tokens":N,"estimated_tokens_delta":D}`
- `{"type":"assistant","message":{...,"content":[{type:"thinking"|"text"|"tool_use",...}],
   "usage":{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}}}`
   — one per model message. We count these as "turns" for the turn budget.
- `{"type":"user","message":{...}}` — tool results fed back (also seen in agentic loops).
- `{"type":"rate_limit_event", ...}` — informational.
- `{"type":"result","subtype":"success"|"error_max_turns"|...,"is_error":bool,
   "total_cost_usd":float,"num_turns":int,"result":"<final text>","usage":{...},
   "modelUsage":{...},"permission_denials":[...],"terminal_reason":"completed"}` — terminal.

**Cost of record:** `result.total_cost_usd` is authoritative and is what we accumulate
against budgets (R5/R9). We also track running cost mid-stream from per-message `usage`
only as a *display estimate*; the `result` event reconciles it.

**Tolerant parser (R/§3):** unknown `type`/`subtype` values are wrapped in an
`UnknownEvent` and never raise — the TUI must not crash on schema drift.

---

## 1. Language / stack
- **Python 3.11+** (dev box has 3.13.7), **Textual** for the TUI, **PyYAML** for config &
  frontmatter, **pytest + pytest-asyncio** for tests, `git`/`claude` CLIs via asyncio subprocess.
- Packaged with a PEP 621 `pyproject.toml` (hatchling). Installs via `pipx install .` (or
  `uv tool install .`) and exposes one console script: `foreman`.
- No database. All durable state is human-readable files under the target repo's `.foreman/`
  (R4). Foreman holds only derived/in-memory state, always rebuildable from disk.

## 2. The AgentBackend seam (testability + R1)
The single most important seam. `AgentBackend.run(spec) -> AsyncIterator[StreamEvent]`.
- `ClaudeBackend` spawns the real `claude` CLI (R1).
- `MockBackend` replays canned stream-json fixtures keyed by phase/skill, so the entire
  state machine, pipeline, scheduler and TUI are exercised without burning tokens
  (§11.5, §12 demo). `foreman demo` and most tests use it.
This is the highest seam (per the upstream PRD philosophy) — everything above it is backend-agnostic.

## 3. Skill invocation (§6 "one SkillInvocation helper")
All phases reference only the **vendored, namespaced** skills (`foreman-grill-docs`,
`foreman-to-prd`, `foreman-to-issues`, `foreman-tdd`) — never the upstream names (R2, §12).
`SkillInvocation.build_prompt()` is the single place that turns a phase + context into the
`-p` prompt text. It invokes a skill by writing an explicit instruction
`Use the Skill tool to run the "foreman-xxx" skill.` plus the `/foreman-xxx` slash form, then
the task context. Encapsulated so the mechanism is fixable in one place if Claude Code changes.

## 4. Documents, approvals & the human gate (R3)
`plan.md`, `adr.md`, `prd.md` are documents with YAML frontmatter holding `version`,
`status` (drafting → in_review → changes_requested → approved), and an `approval` block
(reviewer, timestamp, sha256 of the *body at approval time*). On every load we recompute the
body hash; if it differs from the recorded approval hash, the approval is auto-invalidated and
status reverts to `in_review` (R3). Approval is therefore a pure function of file contents —
fully crash-safe (R4). Nothing in Phase B may start unless `prd.md` is `approved` AND the
queue has been explicitly confirmed (R3/§6/§12).

## 5. Issues & traceability (§5)
Issue files carry the §5 frontmatter (`id, title, status, depends_on, branch, attempts,
budget, prd_refs`). Statuses: queued | in_progress | tests_failing | needs_human | done | merged.
The slicer (`foreman-to-issues`) emits them; the scheduler mutates only `status`/`attempts`.

## 6. The scheduler / Boris loop (§7)
A pure-async loop: pick ready issues (queued + all `depends_on` done), spawn up to
`max_parallel` workers each in its own **git worktree** on the issue branch, stream events,
enforce budgets, and on completion **re-run the configured test/lint/typecheck commands
ourselves** (never trust the agent's claim — §7/§12) cross-checked against the worker's
machine-readable summary block. Pass → commit + merge to integration branch (or open PR).
Fail → attempts++ and re-spawn with failing output appended, until `max_retries`, then
`needs_human`. Budget/timeout breach → kill + escalate. Killing a worker rolls its worktree
back clean (R/§7). Stuck detection: no file changes + no test progress across N turns → escalate.

## 7. Guardrails (R5/§9)
Enforced by Foreman, not the agent: per-run `max_turns` (count assistant turns → kill),
`max_cost_usd` (parse usage → kill + native `--max-budget-usd`), `timeout_min` (asyncio → kill);
per-issue `max_retries` (default 3); global `max_parallel` (default 2) and `daily_cost_usd`
hard stop. Every enforcement event is logged to the run record and surfaced in the TUI.

## 8. Vendored skills, what changed from upstream (per §11.3, §4)
See `NOTICE` for attribution. Forked from mattpocock/skills @ HEAD (cloned 2026-06-12). The
four upstream skills are `grill-with-docs`, `to-prd`, `to-issues`, `tdd` plus the setup skill.

- **foreman-grill-docs** (from `grill-with-docs` + `to-prd`): Upstream runs a *live, one-
  question-at-a-time interview*. There is no live user in Foreman, so the fork (a) explores the
  target codebase, CONTEXT.md and `docs/adr/` to self-answer every question it can, and
  (b) emits the residual questions as a structured **"## Open questions for reviewer"** block
  at the TOP of the ADR/PRD draft. Reviewer answers arrive as review comments; the next pass
  consumes them, resolves those branches, and surfaces newly-uncovered questions. Done only at
  zero open questions + approval. Writes BOTH `adr.md` and `prd.md` into the feature dir, and
  keeps the target repo's own CONTEXT.md / docs/adr/ updated inline. Strips all "ask one at a
  time / wait for feedback" interactive language. Carries `ADR-FORMAT.md` + `CONTEXT-FORMAT.md`.
- **foreman-to-prd** (from `to-prd`): kept as the PRD template/section authority used by the
  grill skill; strips the `gh`/issue-tracker "publish + apply ready-for-agent label" step and
  the "check with the user" seam confirmation (replaced by the open-questions block). Output is
  the file `prd.md`, not a tracker post.
- **foreman-to-issues** (from `to-issues`): emits LOCAL issue files matching the §5 schema into
  `.foreman/features/<slug>/issues/ISS-NNN.md` — no `gh`, no GitHub labels/triage vocab, no
  "quiz the user" interactive loop (the queue-review TUI screen replaces it). Preserves the
  vertical-slice / tracer-bullet philosophy, dependency ordering, and PRD §-traceability via the
  `prd_refs` frontmatter field.
- **foreman-tdd** (from `tdd`): stack-agnostic — test/lint/typecheck commands come from
  `config.yaml` (injected into the prompt), not hard-coded npm/Husky. Takes the issue file as
  the slice definition. Keeps strict red-green-refactor, one vertical slice at a time (no
  horizontal slicing). **Ends every run by emitting a fenced ```json FOREMAN-SUMMARY block**
  (files_touched, tests_added, test/lint/typecheck command results, open_concerns) that Foreman
  parses. Strips interactive "confirm with user / get user approval" steps → those become
  escalation triggers. Carries `tests.md` (good/bad tests) as supporting material.

Each vendored skill's frontmatter gets a `foreman_skill_version: <N>` marker. `foreman init`
installs them into the target repo's `.claude/skills/`; startup verifies versions and offers a
one-key update, only ever overwriting Foreman's own `foreman-*` skills.

## 9. Open assumptions (documented, not blocking)
- "Maximum reasoning effort" in headless mode == `--effort max` (or config value). Confirmed flag.
- Turn counting proxy: each `type:"assistant"` message == one turn for the `max_turns` budget.
  This slightly over-counts vs the final `num_turns` but is conservative (kills earlier), which
  is the safe direction for a guardrail.
- `merge_strategy: merge` default; `open_pr:false` default (we have no GitHub dep in v1, §13).
  When `open_pr` is true with no remote, we degrade to creating the branch + a PR-body file and
  surface a notice rather than failing.
- Git identity: if the target repo has no user.name/email, Foreman commits with a local
  `-c user.name=Foreman -c user.email=foreman@localhost` override scoped to its own commits.
