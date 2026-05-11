# State-file transition timeline

Disk-truth transitions the harness waited on, with real-time latency.

| when | feature | transition | latency_s | detail |
|------|---------|------------|-----------|--------|
| 2026-06-15T21:25:10Z | add-done-command | planner | 0.2 |  |
| 2026-06-15T21:25:12Z | add-done-command | grill | 0.2 |  |
| 2026-06-15T21:25:13Z | add-done-command | grill | 0.2 |  |
| 2026-06-15T21:25:15Z | add-done-command | slicer | 0.3 |  |
| 2026-06-15T21:25:15Z | add-done-command | queue_confirmed | 0.0 |  |
| 2026-06-15T21:25:22Z | add-done-command | build_round_0 | 6.8 | {'merged': 2} |
