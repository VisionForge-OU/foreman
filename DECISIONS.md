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
fully crash-safe (R4). Nothing in Phase B may start unless both `adr.md` and `prd.md`
are `approved` AND the queue has been explicitly confirmed (R3/§6/§12).

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

---

# PHASE 2 — "Trustworthy Autonomy"

Phase 2 upgrades Foreman from *automated* to *trustworthy*: executable verification,
builder/grader separation, disciplined context, correct parallelism, low-fatigue review,
and a self-improvement flywheel. This part records the Phase-2 decisions, the empirical
findings they rest on, and the schema-v2 migration. Phase-1 behaviour is preserved; every
`.foreman/` tree from Phase 1 keeps working via the migration in §P2.2.

## P2.0 Empirical verification of the mechanics Phase 2 depends on

Re-verified against `claude` v2.1.174 on 2026-06-12 with **real headless runs** in throwaway
scratch repos (each run cost ≈ $0.02 on `--model haiku --effort low`). Evidence, not docs.

### Hooks (the WS1 evidence gate rests entirely on these)
- **`PreToolUse` config shape** lives in `.claude/settings.json` under
  `hooks.PreToolUse = [{ "matcher": "Write|Edit", "hooks": [{ "type": "command", "command": "<abs path>" }] }]`.
  The matcher is a regex over the **tool name**. The hook command receives the event as JSON
  on **stdin**: `{ "tool_name": "Write", "tool_input": { "file_path": "...", "content": "..." }, ... }`.
- **A `permissionDecision: "deny"` blocks the tool even under a permissive permission mode.**
  Verified: with `--permission-mode acceptEdits`, a hook emitting
  `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"…"}}`
  on stdout caused the `Write` to `verification.json` to **never happen** (file absent on disk);
  the reason text was fed back to the model as the tool result. This is the exact guarantee
  WS1.3 needs and it holds under the mode workers actually run with (`acceptEdits`).
- **Exit-code-2 also blocks**, with the hook's **stderr** delivered to the model as the tool
  result (`PreToolUse:Bash hook error: …`). This is the mechanism the cwc-long-running-agents
  hook scripts use; we use it for Bash-path gating where a path can't be read from a single
  JSON field (e.g. a `bash -c 'echo … > verification.json'` write).
- **Decision:** Foreman's worktree hooks emit the structured `permissionDecision: deny` JSON
  for the Write/Edit case (clean, no stderr noise) and fall back to `exit 2 + stderr` for the
  Bash case. Both are proven.

### Subagents (the WS2 evaluator / WS5 auditor rest on these)
- A `.claude/agents/<name>.md` file with YAML frontmatter (`name`, `description`,
  `tools: Read, Grep, Glob`, `model: haiku`) is invoked headlessly with `claude -p --agent <name>`.
- **The `tools:` allowlist is structurally enforced.** Verified: the `system/init` event for an
  `--agent foreman-evaluator` run reported `tools=['Read','Grep','Glob']`; when asked to create
  a file it had **no Write tool available** and produced nothing on disk. A read-only evaluator
  therefore *cannot* mutate the tree — builder/grader separation is structural, not prompted.
- The init event also exposes the available agent names (`agents=[...,'foreman-evaluator',...]`),
  which Foreman asserts to confirm the agent was installed before trusting a verdict.
- `--agent` selects the session agent; `--model`/`--effort` on the CLI still apply, so Foreman
  can override the evaluator's model per-issue (cheap default, frontier for high-stakes).

### Stop-time enforcement (WS3.2)
- A headless `-p` run simply *ends*; a `Stop` hook cannot meaningfully "hold a worker open" the
  way it can interactively. **Decision:** enforce the handoff contract **Foreman-side** (the
  prompt permits "hook or Foreman-side"): after the run, Foreman checks `progress.md` was updated
  and the worktree builds; a failure is treated as an incomplete attempt and bounced. This is
  more robust than a Stop hook for headless runs and keeps the logic testable above the backend
  seam. A `commit-on-stop`-style git backstop (cwc primitive) is still installed as a Stop hook
  so a killed worker never loses work.

### Settings precedence & coexistence
- **`claude --settings <file>` loads and fires hooks — verified with a real run.** Rather than
  writing into the worktree's tracked `.claude/settings.json` (which would show as a diff and risk
  being committed), Foreman writes its hook scripts + a `settings.json` into a **sibling** dir
  (`<worktree>.foreman-hooks/`) and passes `--settings <that>/settings.json`. Verified: a worker
  given that `--settings` file was **blocked** from writing `verification.json` (file never
  created, deny reason surfaced) — exactly as with an in-repo settings file, but with zero
  worktree-diff pollution. The user's global/user settings still load (no `--strict-mcp-config`),
  so their MCP servers and other skills remain available (R2); Foreman's hooks are additive.
- The runner injects, via the subprocess env: `PATH` (prepended with the hooks dir so
  `foreman-test` resolves), `FOREMAN_TEST_CMD` (the project's real test command the wrapper runs),
  `FOREMAN_WORKER_ID` (seeds the `--fast` subsample), and `FOREMAN_TEST_LOG`.

## P2.1 Architecture integration map (where each workstream lands)

New modules (top-level under `src/foreman/`):
- `verification/` — `checks.py` (acceptance-check model + runner), `evidence.py` (evidence-dir
  contract + validation), `ratchet.py` (regression baseline + gate), `verification_json.py`
  (the Foreman-owned `verification.json` writer — the ONLY writer).
- `hooks/` — packaged hook-script assets + `installer.py` that writes them into a worktree's
  `.claude/`. Assets: `deny_protected.sh` (deny worker writes to verification.json / issue
  status), `commit_on_stop.sh` (git backstop). Plus the installed `foreman-test` wrapper.
- `agents/` — packaged `foreman-evaluator.md` and `foreman-auditor.md` agent files +
  `evaluator.py` / `auditor.py` spawn paths and verdict parsing.
- `context/` — `assembler.py` (the one `ContextAssembler` with per-section token budgets),
  `initializer.py` (one-time feature initializer), `distiller.py` (failure-distiller).
- `retro/` — `metrics.py` (outcome taxonomy + aggregation), `retro.py` (`foreman retro`),
  `bench.py` (`foreman bench`).

Touched modules: `models.py` (new dataclasses + statuses + schema version), `state.py`
(schema-v2 read/write + migration + `verification.json` + outcome labels), `config.py` (new
config blocks), `scheduler.py` (conflict graph, locks, evaluator stage, janitor cadence,
`awaiting_evaluation`), `runner.py` (assembled-prompt token logging), `skill_invocation.py`
(ContextAssembler integration), `installer.py`/`vendored.py` (install hooks + agents +
`foreman-test`; version markers), the vendored skills (`foreman-to-issues`, `foreman-tdd`,
`foreman-grill-docs`), `cli.py` (`retro`/`bench`), `tui/` (review v2, metrics pane, conflict
graph, verdicts, token meters, notify), `demo_scripts.py`/`demo.py` (new demo cases).

## P2.2 State schema v2 + migration (the foundation everything builds on)

- A new top-level marker file `.foreman/schema_version` holds an integer (Phase-1 trees have
  none ⇒ treated as **v1**). `FileStore` reads it on open; a `migrate()` runs lazily and
  idempotently when a v1 tree is loaded, then stamps `2`.
- **v1→v2 migration is purely additive — it never rewrites or deletes Phase-1 files.** It:
  (a) writes `schema_version=2`; (b) for each existing feature with issues, materialises a
  `verification.json` seeded `{<id>: {passes:false, evidence:[], verified_at:null, verified_by:null}}`
  for any issue lacking one (existing MERGED/DONE issues are recorded as `passes:true` with an
  empty-evidence `verified_by:"migration"` note so the ratchet has a baseline). Issue
  `kind`/`touches` are **not** written back to disk — they default **in memory** (`feature` /
  empty footprint), so a missing footprint means "unknown" ⇒ the scheduler treats it as
  conflicting-with-all until re-sliced (the safe default) while the v1 issue file stays
  byte-for-byte untouched. Run `outcome` labels default to `legacy` in memory where absent.
  Migration is covered by `tests/test_migration.py`, which builds a v1 tree (no marker, no
  verification.json), asserts it is detected as v1, migrates on load, and verifies the Phase-1
  issue files are unchanged byte-for-byte and still load with v2 defaults.
- Issue frontmatter gains: `acceptance_check` (path/command — **required** for new issues),
  `touches` (list), `kind` (`feature`|`janitor`). New `IssueStatus.AWAITING_EVALUATION`. The
  enum is parsed tolerantly (unknown ⇒ kept as raw, never crashes) so a forward-rolled tree
  opened by an older Foreman degrades gracefully.
- `verification.json` lives at `.foreman/features/<slug>/verification.json`. **Invariant: only
  `verification_json.py` (Foreman) writes it; workers are blocked by the worktree hook.** It is
  NOT the issue file's `status` — status remains in the issue frontmatter, but the *truth of
  "passes"* is the verification.json entry, which only Foreman flips after its own checks.

## P2.3 Workstream designs (decisions, not just restatement)

**WS1 — executable verification.** Acceptance checks are first-class: `verification/checks.py`
defines `AcceptanceCheck{kind: test_file|command, ref}` stored under `issues/ISS-NNN.check/`.
The slicer (`foreman-to-issues` v2) must emit one per issue derived from a PRD acceptance
criterion; `state.write_issue` rejects an issue with no `acceptance_check` from entering the
queue (queue-review surfaces the check next to the issue). `verify()` is extended so the gate is
**acceptance-check pass AND full-suite ≥ baseline AND lint/typecheck clean**; the regression
ratchet (`ratchet.py`) snapshots the set of passing test ids after each merge and a newly-failing
test bounces the work *naming the regressed tests*. The completion contract: the worker saves
evidence under `runs/<id>/evidence/`; `evidence.py` rejects a "complete" summary whose listed
artifacts are missing/empty and Foreman counts it as a failed attempt. `foreman-test` is a small
installed wrapper: ≤20-line console (counts + failures only), full log with one `ERROR`-prefixed
greppable line per failure, pre-computed stats, and `--fast` = deterministic per-worker seeded
subsample (seed = hash(worker id)); the full suite is still mandatory before completion. The
wrapper prints elapsed wall-clock and the `foreman-tdd` prompt caps the share of turns spent
re-running tests.

**WS2 — evaluator.** `agents/foreman-evaluator.md` (`tools: Read, Grep, Glob`, model configurable,
cheap default). Graded rubric (functionality / PRD-fidelity / craft / test-honesty, each 1–5 with
justification + concrete objections) emitted as a fenced JSON verdict Foreman parses. Pipeline:
worker done → evidence validation (WS1) → acceptance + ratchet gate (WS1) →
**`awaiting_evaluation`** → evaluator verdict → pass ⇒ merge; objections ⇒ bounce to a *fresh*
worker with the verdict attached (counts toward retries); evaluator uncertainty / repeated
worker-vs-grader disagreement ⇒ escalate with both artifacts. Spawned like a worker via
`--agent foreman-evaluator` with its own smaller budget; verdicts shown in the TUI and stored in
`runs/<id>-eval/verdict.json`. Concrete decisions: (a) Foreman **commits the slice before
grading** so the evaluator reviews a real `integration...HEAD` diff (and reads the worktree
directly via its read-only tools); (b) a verdict is merge-worthy only if `verdict=="pass"` AND
there are no listed objections AND every rubric score ≥ `evaluator_min_score` (default 3) — a
"pass" with a low score or any objection is treated as objections; (c) "repeated disagreement"
is operationalised as **≥2 evaluator objections** on the same issue (or exhausting the retry
ceiling), which escalates with both sides' artifacts; (d) the evaluator runs on a cheaper model
(`model_evaluator`, default Haiku) overridable per high-stakes issue, gated by `evaluator_enabled`.

**WS3 — context architecture.** `context/initializer.py` runs once per feature on "Start build":
writes `init.sh`, confirms the test/lint commands actually run, seeds `feature-state.md`
(status + conventions digest + gotchas); workers run `init.sh` first. Handoff is mandatory and
enforced Foreman-side (P2.0): every session updates `progress.md` and leaves a mergeable tree.
Retries are **fresh-session by default** (`retry_strategy: fresh|resume`, default `fresh`): a
cheap `context/distiller.py` produces a ≤1-page failure report (attempted / exact failing output /
ruled-out hypotheses) and the fresh worker gets issue + acceptance check + failure report +
`progress.md` + `feature-state.md` only. `context/assembler.py` is the single `ContextAssembler`
with an explicit per-section token budget; it includes only the PRD sections named in the issue's
`prd_refs` (never the whole PRD) and the conventions digest (never whole docs). Assembled-prompt
token counts are logged per run and shown in the TUI. Concrete decisions: (a) **`init.sh` is run
by Foreman** in the worktree before each worker (deterministic) with `feature-state.md` fed into
the prompt; (b) the **handoff is enforced as a pre-gate check** — a finished (non-escalating)
session whose `runs/<id>/progress.md` is missing/empty is a failed attempt *before* the merge gate
runs; (c) the distilled failure report is **deterministic** (no model/tokens) and ≤1 page, handed
to the fresh retry alongside the prior `progress.md` + `feature-state.md`; (d) over-budget sections
truncate with a visible marker and `RunRecord.prompt_tokens` carries the assembled size to the TUI
and `usage.json`. The initializer also seeds `RunRecord.outcome` plumbing reused by WS6.

**WS4 — real parallelism.** `foreman-to-issues` v2 emits `touches:` per issue; the scheduler
builds a conflict graph from `touches` overlap and never co-schedules overlapping issues, biasing
dispatch toward maximum parallel width; the queue-review screen renders the graph so the human can
re-slice. Second defence (footprints can lie): file-based locks — a worker's first commit writes
`current_tasks/ISS-XXX.lock` on the integration branch; a push conflict forces the second claimant
to back off; Foreman reclaims stale locks via heartbeat timestamps. Janitor: after every N merges
(default 3) run specialised read-write agents one at a time, each gated by the *same* pipeline
(tests + ratchet + evaluator): dedup, conventions-critic, docs. Janitor work appears as ordinary
issues with `kind: janitor`. Concrete decisions: (a) footprint overlap is **path containment**
(`src/` overlaps `src/a.py`); an empty/unknown footprint conflicts with all; dispatch is a greedy
maximum-independent-set (prefer known-footprint, least-conflicting, id order) that never co-runs an
overlap. (b) **No git remote in v1**, so locks (`locks.py`) are an on-disk `current_tasks/` dir in
the integration worktree (kept out of git via `.git/info/exclude`) with **heartbeat-based stale
reclaim** instead of push-conflict detection — remote-ready for when a remote exists; a live foreign
lock marks the issue lock-blocked for the run (no re-dispatch spin). (c) Janitor passes run at
**quiet points** (no feature worker in flight, `merged_feature // janitor_every` exceeds
passes-run), one specialist at a time; janitors have an unknown footprint (run alone) and no
acceptance check (the gate skips evidence/acceptance for janitors but still enforces suite + ratchet
+ evaluator + handoff); outcomes go to `report.janitor`, never inflating the feature merge count.

**WS5 — spec integrity + review DX.** After all issues merge, a read-only auditor
(`agents/foreman-auditor.md`) walks the PRD requirement-by-requirement → satisfied / diverged /
unimplemented, mapping each to evidence; a divergence yields a **PRD amendment** draft that
re-enters the hash-sealed review gate (approve ⇒ re-seal; reject ⇒ new fix issues). Review v2:
default to **diff-since-my-last-review** (vs the version I last acted on), **open-questions-first**
with inline answer fields that compose the review comment on submit, a ≤10-line *"decisions made
on your behalf"* digest the grill skill emits, and read-time + word-delta badges for triage.
`notify_command` (config) fires on review-needed / escalation with feature/doc/issue id + one-line
reason. Concrete decisions: (a) the auditor runs **once after `_maybe_run_e2e`** on the integration
worktree (`--agent foreman-auditor`, read-only, `model_auditor`/Haiku, reusing the evaluator
budget), only when every feature issue has landed; (b) `needs_amendment` is **divergences only** —
a `diverged` finding drafts a *deterministic* PRD amendment (`audit.build_amendment`, appends a
`## PRD Amendment` section, never edits the original) written as a new `IN_REVIEW` PRD version that
auto-invalidates the prior approval at load (R3) ⇒ it re-enters the hash-sealed gate;
**rejecting** that amendment (request-changes on a PRD carrying the `## PRD Amendment` heading) is
*not* a silent drop — `scheduler.reject_amendment` reloads the persisted audit, keeps the approved
spec (strips the amendment section + re-seals), and turns every diverged/unimplemented finding into a
queued, buildable `FIX-NNN` issue via `fix_issue_bodies` (wired through `controller.request_changes`
and the TUI ReviewScreen, so the human reject is one keystroke); (c) `notify_command` is best-effort (env + arg
payload, 15s timeout, never raises) fired via `notify.fire` from the sync `_escalate` and on the
amendment draft; (d) review-v2 diff is computed against a **body snapshot taken at the reviewer's
last action** (`reviews/<kind>-v<n>-body.md`), since `write_doc` keeps only the latest body — first
review shows the full body; (e) the modules (`audit.py`, `review.py`, `notify.py`) are pure/tested
in isolation and the scheduler/TUI wire them.

**WS6 — evals flywheel.** Run metadata gains the outcome taxonomy
(`success_first_try | success_after_retry(n) | evaluator_bounce | escalated(reason) |
human_rejected(reason)`) + cost/turns/wall-time; the TUI metrics pane renders success rate, mean
retries/issue, cost/issue, escalation histogram, and trends. `foreman retro` clusters recurring
failure patterns from `runs/` and proposes concrete patches to skills / rubric / prompt templates
— drafts that pass through the **same hash-sealed human review gate** as PRDs; approval bumps the
skill version and appends to `SKILL_CHANGELOG.md`. `foreman bench` replays an eval set (issue +
repo snapshot + known-good outcome, mocked by default, optional real-token mode with a cost
ceiling) and attaches a success-rate/cost/turn delta report to every proposed patch — **no patch
lands without a bench report**. Concrete decisions: (a) the outcome label is stamped on the
**terminal** run record (`metrics.label_success(attempts)` / `evaluator_bounce()` /
`escalated(reason)`) and re-persisted; non-issue runs (planner/grill/initializer/evaluator/audit)
stay unlabeled and aggregate as `legacy`; (b) failure clustering is **deterministic** (no model) —
escalation reasons fold into `budget|timeout|regression|handoff|leading-phrase` buckets; (c) retro
proposals are gated by reusing the **PRD hash-seal** (`retro/driver.py`: one frontmatter `.md` per
proposal under `.foreman/retro/`, body-sha256 approval that auto-invalidates on edit); `land`
**refuses** unless the proposal is approved-and-sealed AND a bench report is attached
(`retro.is_landable`); a landed skill patch bumps the **target repo's installed** skill (never
Foreman's packaged distribution) and appends `SKILL_CHANGELOG.md`; (d) `foreman bench` is mocked by
default (no tokens; the injected `runner_factory` replays), real-token mode (`--real`) bounded by
`bench_cost_ceiling_usd` with skipped cases **logged, never silently capped**; (e) the human side of
the gate is driveable from the TUI **RetroScreen** (`t` from the dashboard) — list proposals, inspect
the diff + attached bench delta, and approve / reject / land, with the landing gate surfaced as a
notify error; generation (`foreman retro`) and benchmarking (`foreman bench`) stay on the CLI since
both are long, token-spending agent runs. The build **report** (`report.md`) summarises cost,
**retries** (Σ feature-issue attempts), and escalations so the post-build numbers are scannable.

## P2.4 Deviations from the Phase-2 prompt (with rationale)
- **WS3.2 handoff enforced Foreman-side, not via a Stop hook** — a headless `-p` Stop hook can't
  hold the run open; Foreman-side post-run checks are strictly stronger and testable above the
  backend seam (a `commit-on-stop` git backstop hook is still installed). The prompt explicitly
  allows "hook or Foreman-side".
- **`verification.json` is Foreman-write-only via a path-deny hook**, which is *stricter* than the
  cwc "evidence-before-write" gate (where the agent writes the results file after reading
  evidence). Foreman's evidence contract instead validates the `runs/<id>/evidence/` dir
  Foreman-side. Both the deny hook and the evidence validation are kept; this matches WS1.2's
  "workers are forbidden from writing it".
- Unknown `touches` (Phase-1 migrated issues) are treated as **conflicting-with-all** until
  re-sliced — the safe default for a correctness guarantee, surfaced in the conflict-graph view.
- **WS4.2 locks are an on-disk `current_tasks/` dir + heartbeat reclaim, not git-push-conflict
  detection** — v1 has no remote (§9). The protocol is remote-ready; the lock lives on the
  integration branch's working tree (excluded from git) so it generalises when a remote exists.
- **WS6.3 `foreman bench` real-mode replay through the scheduler is left as the heavier path**; the
  default mocked mode replays recorded outcomes via an injected `runner_factory`. The enforceable
  contract — a bench *report* (delta vs baseline) must be attached before a retro patch can land —
  is fully implemented and tested; wiring per-case full-pipeline replay is a natural follow-up.

## P2.5 Reference evidence base
Designs above draw on: the C-compiler agent post (verifier quality, parallelism locks, agent
specialization, time-blindness, log hygiene), the long-running-agents harness posts (initializer,
incremental sessions, handoff artifacts, planner/generator/evaluator, gradable rubrics), the
context-engineering post (context rot → minimal high-signal tokens), the evals post (eval suites),
and **anthropics/cwc-long-running-agents** — whose concrete primitives we adopt: a read-only
`evaluator.md` agent, a `PreToolUse` verify gate, a `commit-on-stop` git backstop, and the
Default-FAIL results contract (Foreman's `verification.json` seeded `passes:false`).

---

# Architecture deepening (2026-06-15, v0.5.0)

A behaviour-preserving refactor (335 tests green; adversarial diff review found no
behavioural deltas) that turned several shallow modules deep and closed leaking seams.
Recorded here so future architecture reviews don't re-suggest them.

- **The Seal (`seal.py`).** The approval invariant — "approval = sha256 of the body,
  auto-invalidate on edit" (R3) — lives in one module. Both adapters that need it (gated
  documents via `FileStore`, retro proposals via `retro/driver`) call `seal.intact()` /
  `seal.fingerprint()`. `hashing.body_hash` remains the low-level primitive.
- **Prompt continuation (`prompts.py`).** The cross-cutting "CONTINUE — resumed with more
  turns" text (worker / non-worker agent / Phase-A pipeline) and the distilled
  failure-report appendix have one owner. Per-role *base* prompts still live with their
  agents (`context.assembler`, `agents.evaluator`, `audit`, `janitor`, `skill_invocation`).
- **Budget policy (`runner.should_extend`).** One predicate decides "resume the same
  session with more turns vs escalate"; the worker loop, the non-worker agent loop, and the
  pipeline all call it instead of re-deriving the `KILLED_TURNS` + session + cap check.
- **The merge gate (`verification/merge_gate.decide`).** The compound merge verdict
  (evidence + acceptance + suite + ratchet + evaluator + bounce/escalate policy) is ONE
  module returning a `GateDecision` (MERGE | BOUNCE | ESCALATE). The scheduler/`IssueRun`
  only act on it. The evaluator stays a separate read-only `--agent` (injected, §2/WS2);
  the commit that makes the slice reviewable is an injected `on_structural_pass` awaited
  the instant the structural gate passes (before the evaluator diffs the worktree).
- **The issue run (`issue_run.IssueRun`).** One issue's full build lifecycle (lock lease,
  per-attempt worker run, turn-extension, mandatory handoff, merge gate, retry/escalation,
  worktree teardown) is a deep method-object behind `run() -> str`. `scheduler.py` shrank
  1110 → 782 lines and is now a dispatcher; a single issue is testable without the build loop.
- **`FileStore` owns the `.foreman/` layout I/O.** Run artifacts (verdict, audit, report),
  escalations, run progress, usage records, and review snapshots are written/read through
  intent methods; orchestrators no longer construct paths to do file I/O, and `retro/metrics`
  no longer duck-types on `.paths.runs_dir`. `RepoPaths` (`.paths`) remains an internal seam
  for legitimate raw-`Path` needs (subprocess cwd / `extra_dirs`); it was deliberately NOT
  fully privatised (low value, high churn).
- **TUI controller is a facade.** Screens cross one seam via `kill_worker` /
  `escalation_text` / `config_path` / `review_digest`, instead of reaching into
  `controller.scheduler`, `controller.store.paths`, or importing the `review` module. `cli.py`
  remains the composition root and may still wire collaborators directly.
- **`StreamEvent` vocabulary.** Events expose `is_assistant` / `is_result` / `made_progress`,
  and the CLI progress-tool names live in `stream_parser`. Consumers above the backend seam
  (the TUI) no longer import concrete event classes; `runner` (the seam consumer) still
  inspects typed events for accounting.
