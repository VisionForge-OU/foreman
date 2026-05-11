# State-file transition timeline

Disk-truth transitions the harness waited on, with real-time latency.

| when | feature | transition | latency_s | detail |
|------|---------|------------|-----------|--------|
| 2026-06-15T21:34:36Z | return-created-at-in-task-list | planner | 109.0 |  |
| 2026-06-15T21:36:42Z | return-created-at-in-task-list | planner | 99.8 |  |
| 2026-06-15T21:38:57Z | return-created-at-in-task-list | planner | 113.6 |  |
| 2026-06-15T21:42:29Z | return-created-at-in-task-list | planner | 95.3 |  |
| 2026-06-15T21:44:48Z | return-created-at-in-task-list | planner | 89.8 |  |
| 2026-06-15T21:49:37Z | return-created-at-in-task-list | planner | 73.4 |  |
| 2026-06-15T21:58:43Z | return-created-at-in-task-list | planner | 72.7 |  |
| 2026-06-15T22:02:01Z | return-created-at-in-task-list | grill | 167.2 |  |
| 2026-06-15T22:06:00Z | return-created-at-in-task-list | slicer | 181.0 |  |
| 2026-06-15T22:06:40Z | return-created-at-in-task-list | queue_confirmed | 0.0 |  |
| 2026-06-15T22:28:31Z | return-created-at-in-task-list | build_round_0 | 1311.0 | {'merged': 5} |
| 2026-06-15T22:31:00Z | daily-plan-endpoint | planner | 148.2 |  |
| 2026-06-15T22:34:22Z | daily-plan-endpoint | grill | 174.3 |  |
| 2026-06-15T22:37:36Z | daily-plan-endpoint | slicer | 132.8 |  |
| 2026-06-15T22:38:00Z | daily-plan-endpoint | queue_confirmed | 0.0 |  |
| 2026-06-15T22:42:19Z | daily-plan-endpoint | build_round_0 | 259.3 | {'needs_human': 1, 'queued': 2} |
| 2026-06-15T22:42:31Z | daily-plan-endpoint | build_round_1 | 0.4 | {'tests_failing': 1, 'queued': 2} |
| 2026-06-15T22:42:46Z | daily-plan-endpoint | build_round_2 | 0.3 | {'queued': 3} |
| 2026-06-15T22:42:58Z | daily-plan-endpoint | build_round_3 | 0.4 | {'queued': 3} |
| 2026-06-15T22:45:24Z | task-priority-and-due-date | planner | 137.8 |  |
| 2026-06-15T22:47:46Z | task-priority-and-due-date | planner | 84.5 |  |
| 2026-06-15T22:51:30Z | task-priority-and-due-date | grill | 181.0 |  |
| 2026-06-15T22:55:47Z | task-priority-and-due-date | grill | 198.2 |  |
| 2026-06-15T23:01:35Z | task-priority-and-due-date | grill | 289.4 |  |
| 2026-06-15T23:06:21Z | backlog-aging | planner | 231.2 |  |
| 2026-06-15T23:09:07Z | easier-mornings | planner | 8.5 |  |
| 2026-06-16T15:19:31Z | daily-plan-endpoint | planner | 99.1 |  |
| 2026-06-16T15:22:10Z | daily-plan-endpoint | grill | 123.9 |  |
| 2026-06-16T15:25:13Z | daily-plan-endpoint | slicer | 120.2 |  |
| 2026-06-16T15:25:37Z | daily-plan-endpoint | queue_confirmed | 0.0 |  |
| 2026-06-16T15:40:18Z | daily-plan-endpoint | build_round_0 | 838.6 | {'needs_human': 1, 'queued': 4} |
| 2026-06-16T15:40:36Z | daily-plan-endpoint | build_round_1 | 0.4 | {'in_progress': 1, 'queued': 4} |
| 2026-06-16T15:40:48Z | daily-plan-endpoint | build_round_2 | 0.5 | {'queued': 5} |
| 2026-06-16T15:41:01Z | daily-plan-endpoint | build_round_3 | 0.6 | {'queued': 5} |
