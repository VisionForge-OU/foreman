"""The evals flywheel (Phase-2 WS6).

Three self-contained, stdlib-only modules that close the improvement loop:

- :mod:`metrics` — the run-outcome taxonomy plus aggregation of ``runs/*/usage.json``
  into a feature-level :class:`~foreman.retro.metrics.Metrics` panel (success rate,
  mean retries/issue, cost/issue, escalation histogram, trends) for the TUI.
- :mod:`retro` — ``foreman retro``: deterministically clusters recurring failure
  patterns from the run records and turns an analysis agent's proposals into
  concrete skill/rubric/prompt patch drafts that pass through the SAME hash-sealed
  human-review gate as a PRD before they ever land.
- :mod:`bench` — ``foreman bench``: replays a known-good eval set (issue + repo
  snapshot + expected outcome), mocked by default with an optional cost ceiling,
  and produces a success-rate/cost/turn delta report. *No skill patch lands
  without a bench report* (WS6).

Everything here reads the ``RunRecord.outcome`` labels the scheduler sets at each
terminal point; this package never writes those labels itself.
"""

from __future__ import annotations

from . import bench, metrics, retro

__all__ = ["metrics", "retro", "bench"]
