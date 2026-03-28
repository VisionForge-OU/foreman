"""Foreman — a Boris-style agentic orchestrator TUI for Claude Code.

Foreman supervises headless `claude` CLI agents through a gated delivery
pipeline (plan -> ADR/PRD -> issues -> TDD build -> e2e), pointed at any repo.
All durable state lives as human-readable files under a target repo's
``.foreman/`` directory; nothing is stored in a database.
"""

__version__ = "0.4.0"

# Version marker stamped into every vendored skill's frontmatter and verified at
# startup. Bump when any vendored skill changes so `foreman init` can offer an
# update of its own ``foreman-*`` skills in target repos.
SKILLS_VERSION = 1
