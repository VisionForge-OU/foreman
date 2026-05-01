"""Foreman's read-only agent files + their installer and verdict parsing (P2.3 WS2/WS5).

- ``installer`` installs the packaged ``foreman-*`` agent ``.md`` files into a
  target repo's ``.claude/agents/`` (namespaced, version-marked, like the skills).
- ``evaluator`` owns the grading prompt and the graded JSON verdict.
- ``auditor`` (WS5) owns the PRD requirement-by-requirement audit.

Agents are structurally read-only (``tools: Read, Grep, Glob``) — empirically
verified to have no Write tool, so a grader can never mutate the tree (§P2.0).
"""

from __future__ import annotations

from . import evaluator, installer, reviewer, security

__all__ = ["evaluator", "installer", "reviewer", "security"]
