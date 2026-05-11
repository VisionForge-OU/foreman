# METRICS — scorecard

_Unattended Foreman Phase-1+2 campaign on `dayplan` (FastAPI+SQLite), haiku
workers, `max_turns=30`, `max_cost_usd=1.50/run`, `permission_mode=acceptEdits`._

## Spend (real tokens)
| pool | $ |
|------|---|
| Foreman workers/agents (on disk) | ~$5.97 |
| Auto-reviewer LLM judges | ~$1.02 |
| C1 plain baseline | $0.11 |
| **cleanly ledgered** | **~$6.19** |
| + validation/retry overhead not itemized (deleted feature dirs, probes, killed batches) | ~$2.5 |
| + F1 payoff re-run | ~$2 |
| **campaign total (est.)** | **~$9–11 of the $60 ceiling (~18%)** |

Cost ceiling was never the binding constraint — **wall-clock and the subscription
session limit were.** Per-worker cost carries a **~$0.02 floor** from the user's
global `SessionStart` hooks + global-context cache re-load on every `claude -p`
(measured on a no-op probe: $0.0229, ~27k cache tokens).

## C1 baseline (plain `claude -p`, no pipeline)
| metric | value |
|--------|-------|
| feature | F5 trivial ("return created_at in GET /tasks") |
| wall | **53.7 s** |
| cost | **$0.111** |
| turns | 11 |
| first-try success | ✅ (tests pass, field returned, correct scope) |

## Per-feature (full pipeline)
| key | type | outcome | wall | cost | issues | notes |
|-----|------|---------|------|------|--------|-------|
| F5 | trivial | ✅ **done** | 1861 s (31 m) | $3.23 | 5 merged | over-sliced 1-liner into 5 issues |
| F1 | greenfield | ⚠️ **build:stuck** | 874 s | $1.83 | ISS-001 `needs_human`, 3 blocked | turn-budget + missing-handoff escalation loop; rescued 4× but unrecoverable |
| F2 | brownfield | ❌ **failed:doc_review** | — | ~$2.0 | — | mandated PRD request-changes + demanding judge never converged in 2 cycles |
| F3 | multi | ❌ **failed:grill** | — | ~$0.7 | — | planner ok; grill `killed_turns` then errored |
| F4 | vague | ❌ **failed:grill** | — | ~$0 | — | **hit 429 session limit** (`resets 3:40am`) — environmental, not pipeline |

**Completion rate: 1/5 fully done.** Of the 4 non-completions, 1 is a real
build-stuck (F1), 1 a review-convergence failure (F2), 1 a grill turn-kill (F3),
and 1 purely environmental quota (F4).

## Outcome distribution (all 43 agent runs)
| terminal_reason | count |
|-----------------|-------|
| **killed_turns** | **21 (49%)** |
| completed | 19 |
| error (429 / api) | 3 |

| Phase-2 outcome label | count |
|-----------------------|-------|
| `success_first_try` | 5 |
| **blank / `legacy` (unlabelled)** | **38 (88%)** |

→ The outcome taxonomy labels almost nothing. The dominant failure
(`killed_turns`) is **never** assigned a failure outcome, so it is invisible to
`foreman retro`. (See ITERATION_REPORT #2.)

## Head-to-head — F5 trivial: pipeline vs plain baseline
| | plain baseline | full pipeline | ratio |
|--|----------------|---------------|-------|
| cost | $0.111 | $3.23 | **~29× more** |
| wall | 53.7 s | 1861 s | **~35× slower** |
| issues | 1 session | 5 issues (incl. scope-creep + 2 speculative test issues) | — |
| result | correct, in-scope | correct, but over-built | — |

**For trivial work the pipeline costs ~29× the money and ~35× the time and
over-engineers the change.** The gate/plan/grill/slice overhead and per-issue
worker+evaluator+merge cycles dominate; none of it is justified for a one-liner.

## Vague feature (F4) — did grilling earn its cost?
**Unanswerable this run** — F4 never produced a real plan/PRD because it hit the
429 session limit immediately (planner+grill both errored at $0). Deferred to a
re-run after quota reset.

## Auto-reviewer activity (synthetic human)
| action | count |
|--------|-------|
| approve | 16 |
| request_changes | 8 |
| escalation answer | 4 |
| reject (retro proposal) | 1 |

Both approve and request-changes branches exercised on real drafts; escalation
rescue exercised 4× (F1/ISS-001); retro patch gate exercised both approve+reject.
See `AUTOREVIEW_LOG.md` for every decision + rationale.

## Payoff run
F1 (greenfield — the feature that hit the missing-handoff failure) was re-run after
landing the foreman-tdd v4 ("handoff-first") patch. **Caveat (verified post-hoc):**
the next run's skill reinstall **reverted the patch to v3** (BLOCKER-2b), so the
payoff ran *without* it — the comparison is confounded. F1/ISS-001 still hit the
same turn-budget grind (6+ `killed_turns`, `tests_failing`). **Result: no measurable
change** — valid and now doubly explained (root cause = 30-turn budget; patch wasn't
even present). Detail in `FLYWHEEL.md`.

> Note: campaign figures here (e.g. `killed_turns` 21/43 = 49%) are for the
> authoritative **5-feature campaign** (`campaign-state.pre-payoff.json`). Including
> the extra payoff runs the rate is 28/54 = 51% — same story, more data.
