"""Context architecture: fresh sessions, structured handoffs, minimal prompts (WS3).

Context is finite and degrades with length (context rot). Foreman engineers the
smallest high-signal context per session rather than letting sessions grow:

- ``assembler`` — the single :class:`ContextAssembler` that builds every worker
  prompt from explicitly-budgeted sections and reports per-section token counts.
- ``distiller`` — turns a failed attempt into a ≤1-page failure report (what was
  attempted, the exact failing output, hypotheses ruled out) for a *fresh* retry.
- ``initializer`` — the one-time per-feature bootstrap (``init.sh`` +
  ``feature-state.md``) every session starts from.
"""

from __future__ import annotations

from . import assembler, distiller, initializer

__all__ = ["assembler", "distiller", "initializer"]
