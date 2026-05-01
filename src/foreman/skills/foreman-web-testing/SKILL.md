---
name: foreman-web-testing
description: Headless end-to-end / web-app testing for the Foreman e2e stage. Derive end-to-end tests from the PRD's User Flows, drive the real application the way a user would (browser flows, screenshots, accessibility checks for web apps), make them pass via the configured e2e command, and emit the FOREMAN-SUMMARY block Foreman parses.
foreman_skill_version: 1
---

# foreman-web-testing

(Adapted from Anthropic's `web-app-testing` skill and the e2e half of `foreman-tdd`
— see NOTICE. Made stack-agnostic: the e2e runner is injected by Foreman from
`config.yaml` (`commands.e2e`), not hard-coded to Playwright; for non-browser projects
the same flow-driven discipline applies through whatever e2e command the project
declares. Added Foreman's evidence + FOREMAN-SUMMARY contract.)

You run **headless** in the integration worktree after every issue has landed. Your
job is to prove the *whole feature works end-to-end* along the journeys the PRD
promised — not to re-run unit tests. Implement the e2e tests, make them pass, save
evidence, then stop with exactly one FOREMAN-SUMMARY block.

## Inputs (injected by Foreman in the prompt)

- The approved **PRD body** — its **`## User Flows`** section is your test charter.
- The **e2e command** for the project (`commands.e2e`, e.g. `npx playwright test` or
  `pytest -m e2e`). Foreman re-runs it itself to verify, so your tests must actually
  pass under it.
- The **evidence directory** you must populate before claiming done.

## Workflow

### 1. Derive flows from the PRD

Turn each user flow into a concrete end-to-end scenario: the precondition, the steps a
real user takes, and the observable outcome ("given A, when B, then C"). Cover the
happy path **and** the obvious failure path the flow implies (invalid input, empty
state, permission denied). Do not test through internal functions — exercise the
application through its real surface (the running web app, the CLI, the HTTP API).

### 2. Drive the real application

- **Web apps:** drive a real browser with the project's e2e tooling. Interact the way
  a user does — locate elements by role/label/text, not brittle CSS nth-child paths;
  wait on a real condition (an element, a network response), never a fixed sleep.
  Check the basics a user would feel: the page renders, the primary action works, no
  console errors, and reasonable accessibility (labelled controls, focus order).
- **Non-web:** drive the same flow through the configured e2e command's surface (API
  requests, CLI invocations), asserting on real observable output.

Capture a **screenshot (or output transcript) per flow** as you go — these are your
evidence.

### 3. Make them pass, then verify

Run the e2e command and read its output. Iterate until every derived flow passes. A
flow that can't be made to pass because the shipped feature doesn't actually deliver
it is a real finding — report it (see escalation below) rather than weakening the test
to go green.

### 4. Save evidence and summarise

Save into the evidence directory Foreman gave you: the e2e run log, and a screenshot
(web) or output transcript (non-web) per flow. List every artifact in the
FOREMAN-SUMMARY `evidence` array — an unbacked completion claim is rejected, and
Foreman re-runs the e2e command itself regardless of what you claim.

## Required output: FOREMAN-SUMMARY

End with exactly one fenced `json` block, `issue_id: "e2e"`, on Foreman's
`foreman-summary/v1` schema (same shape `foreman-tdd` emits): `files_touched`,
`tests_added`, `commands.e2e` (ran/passed/output_tail), `evidence`, `open_concerns`,
and — if a promised flow genuinely cannot pass against the shipped feature — `escalate:
true` with a one-line `escalation_question`. Set `escalate: false` when the flows pass.
Nothing after the block.
