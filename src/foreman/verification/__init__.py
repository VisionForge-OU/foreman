"""Phase-2 executable verification & evidence-gated "done" (P2.3 WS1).

The trust boundary made structural:

- ``verification_json`` is the *only* writer of a feature's ``verification.json``
  (the Default-FAIL structural-done map). Workers are additionally blocked from
  writing it at runtime by a worktree ``PreToolUse`` hook (``foreman.hooks``).
- ``checks`` models and runs each issue's executable acceptance check (WS1.1).
- ``evidence`` enforces the completion contract: a "complete" claim with missing
  or empty evidence artifacts is rejected (WS1.3).
- ``ratchet`` maintains the per-feature passing-test baseline and detects
  regressions, naming the newly-failing tests (WS1.4).
"""

from __future__ import annotations
