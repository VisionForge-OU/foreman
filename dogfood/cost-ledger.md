# Cost ledger

Every worker/agent/judge run appended as it completes. `real` = real `claude` tokens spent; `mock` = MockBackend (free).

| when | feature | label | source | model | real? | cost_usd | turns | note |
|------|---------|-------|--------|-------|-------|----------|-------|------|
| 2026-06-15T21:31:27Z | F5-baseline | plain-claude | baseline | claude-haiku-4-5-20251001 | real | 0.1114 | 11 | C1 plain baseline |
| 2026-06-15T21:43:12Z | return-created-at-in-task-list | judge:plan | harness-judge | claude-haiku-4-5-20251001 | real | 0.0487 | 0 | auto-reviewer judgment |
| 2026-06-15T21:49:57Z | return-created-at-in-task-list | judge:plan | harness-judge | claude-haiku-4-5-20251001 | real | 0.0343 | 0 | auto-reviewer judgment |
| 2026-06-15T21:59:09Z | return-created-at-in-task-list | judge:plan | harness-judge | claude-haiku-4-5-20251001 | real | 0.0417 | 0 | auto-reviewer judgment |
| 2026-06-15T22:02:15Z | return-created-at-in-task-list | judge:adr | harness-judge | claude-haiku-4-5-20251001 | real | 0.0314 | 0 | auto-reviewer judgment |
| 2026-06-15T22:02:54Z | return-created-at-in-task-list | judge:prd | harness-judge | claude-haiku-4-5-20251001 | real | 0.0477 | 0 | auto-reviewer judgment |
| 2026-06-15T22:06:39Z | return-created-at-in-task-list | judge:queue | harness-judge | claude-haiku-4-5-20251001 | real | 0.0483 | 0 | auto-reviewer judgment |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | planner | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0738 | 12 | completed |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | grill | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1857 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | grill | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1243 | 7 | completed |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | slicer | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1908 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | slicer | foreman-worker | claude-haiku-4-5-20251001 | real | 0.2648 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | slicer | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0264 | 3 | completed |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | init | foreman-worker | claude-haiku-4-5-20251001 | real | 0.2206 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1791 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1351 | 12 | success_first_try |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-001-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0971 | 21 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-001-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0617 | 6 | completed |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-002 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1818 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-002 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1254 | 11 | success_first_try |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-002-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0696 | 21 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-002-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0288 | 3 | completed |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-003 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1863 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-003 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1048 | 6 | success_first_try |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-003-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1140 | 21 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-003-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0292 | 1 | completed |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-004 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1854 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-004 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1060 | 8 | success_first_try |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-004-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0726 | 21 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-004-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0631 | 3 | completed |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-005 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1847 | 31 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-005 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0974 | 7 | success_first_try |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-005-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0838 | 21 | killed_turns |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | ISS-005-eval | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0341 | 3 | completed |
| 2026-06-15T22:31:22Z | daily-plan-endpoint | judge:plan | harness-judge | claude-haiku-4-5-20251001 | real | 0.0406 | 0 | auto-reviewer judgment |
| 2026-06-15T22:34:42Z | daily-plan-endpoint | judge:adr | harness-judge | claude-haiku-4-5-20251001 | real | 0.0343 | 0 | auto-reviewer judgment |
| 2026-06-15T22:35:18Z | daily-plan-endpoint | judge:prd | harness-judge | claude-haiku-4-5-20251001 | real | 0.0373 | 0 | auto-reviewer judgment |
| 2026-06-15T22:38:00Z | daily-plan-endpoint | judge:queue | harness-judge | claude-haiku-4-5-20251001 | real | 0.0378 | 0 | auto-reviewer judgment |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | planner | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1515 | 31 | killed_turns |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | planner | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1141 | 7 | completed |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | grill | foreman-worker | claude-haiku-4-5-20251001 | real | 0.2370 | 31 | killed_turns |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | grill | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1455 | 6 | completed |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | slicer | foreman-worker | claude-haiku-4-5-20251001 | real | 0.2691 | 31 | killed_turns |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | slicer | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0887 | 1 | completed |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | init | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1665 | 31 | killed_turns |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1347 | 21 | killed_turns |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.2336 | 31 | killed_turns |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.2937 | 31 | escalated(turn budget exhausted after 2 extension(s)) |
| 2026-06-15T22:42:30Z | daily-plan-endpoint | judge:escalation:ISS-001 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0286 | 0 | auto-reviewer judgment |
| 2026-06-15T22:42:31Z | daily-plan-endpoint | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0000 | 0 | completed |
| 2026-06-15T22:42:45Z | daily-plan-endpoint | judge:escalation:ISS-001 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0307 | 0 | auto-reviewer judgment |
| 2026-06-15T22:42:57Z | daily-plan-endpoint | judge:escalation:ISS-001 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0283 | 0 | auto-reviewer judgment |
| 2026-06-15T22:43:04Z | daily-plan-endpoint | judge:escalation:ISS-001 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0281 | 0 | auto-reviewer judgment |
| 2026-06-15T22:46:15Z | task-priority-and-due-date | judge:plan | harness-judge | claude-haiku-4-5-20251001 | real | 0.0612 | 0 | auto-reviewer judgment |
| 2026-06-15T22:48:23Z | task-priority-and-due-date | judge:plan | harness-judge | claude-haiku-4-5-20251001 | real | 0.0452 | 0 | auto-reviewer judgment |
| 2026-06-15T22:51:54Z | task-priority-and-due-date | judge:adr | harness-judge | claude-haiku-4-5-20251001 | real | 0.0387 | 0 | auto-reviewer judgment |
| 2026-06-15T22:52:23Z | task-priority-and-due-date | judge:prd | harness-judge | claude-haiku-4-5-20251001 | real | 0.0435 | 0 | auto-reviewer judgment |
| 2026-06-15T22:56:15Z | task-priority-and-due-date | judge:adr | harness-judge | claude-haiku-4-5-20251001 | real | 0.0410 | 0 | auto-reviewer judgment |
| 2026-06-15T22:56:41Z | task-priority-and-due-date | judge:prd | harness-judge | claude-haiku-4-5-20251001 | real | 0.0431 | 0 | auto-reviewer judgment |
| 2026-06-15T23:01:57Z | task-priority-and-due-date | judge:adr | harness-judge | claude-haiku-4-5-20251001 | real | 0.0375 | 0 | auto-reviewer judgment |
| 2026-06-15T23:02:25Z | task-priority-and-due-date | judge:prd | harness-judge | claude-haiku-4-5-20251001 | real | 0.0423 | 0 | auto-reviewer judgment |
| 2026-06-15T23:06:53Z | backlog-aging | judge:plan | harness-judge | claude-haiku-4-5-20251001 | real | 0.0534 | 0 | auto-reviewer judgment |
| 2026-06-16T15:16:24Z | flywheel | judge:proposal:p001 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0627 | 0 | patch-gate review |
| 2026-06-16T15:16:42Z | flywheel | judge:proposal:p002 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0299 | 0 | patch-gate review |
| 2026-06-16T15:19:52Z | daily-plan-endpoint | judge:plan | harness-judge | claude-haiku-4-5-20251001 | real | 0.0368 | 0 | auto-reviewer judgment |
| 2026-06-16T15:22:33Z | daily-plan-endpoint | judge:adr | harness-judge | claude-haiku-4-5-20251001 | real | 0.0384 | 0 | auto-reviewer judgment |
| 2026-06-16T15:22:56Z | daily-plan-endpoint | judge:prd | harness-judge | claude-haiku-4-5-20251001 | real | 0.0409 | 0 | auto-reviewer judgment |
| 2026-06-16T15:25:36Z | daily-plan-endpoint | judge:queue | harness-judge | claude-haiku-4-5-20251001 | real | 0.0402 | 0 | auto-reviewer judgment |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | planner | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1661 | 31 | killed_turns |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | planner | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0700 | 2 | completed |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | grill | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1923 | 31 | killed_turns |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | grill | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0827 | 2 | completed |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | slicer | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1964 | 31 | killed_turns |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | slicer | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1432 | 10 | completed |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | init | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1623 | 31 | killed_turns |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.1326 | 21 | killed_turns |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.2378 | 31 | killed_turns |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.0922 | 12 | completed |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | ISS-001 | foreman-worker | claude-haiku-4-5-20251001 | real | 0.2373 | 31 | escalated(turn budget exhausted after 2 extension(s)) |
| 2026-06-16T15:40:35Z | daily-plan-endpoint | judge:escalation:ISS-001 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0291 | 0 | auto-reviewer judgment |
| 2026-06-16T15:40:45Z | daily-plan-endpoint | judge:escalation:ISS-001 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0284 | 0 | auto-reviewer judgment |
| 2026-06-16T15:40:59Z | daily-plan-endpoint | judge:escalation:ISS-001 | harness-judge | claude-haiku-4-5-20251001 | real | 0.0285 | 0 | auto-reviewer judgment |
