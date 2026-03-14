"""Feature initializer — the one-time per-feature bootstrap (WS3.1).

Before any worker runs, Foreman spawns an initializer agent once per feature that:
- writes ``init.sh`` — an environment bootstrap any session can run first;
- confirms the configured test/lint commands actually work in the repo;
- seeds ``feature-state.md`` — current status, a conventions digest, and gotchas.

Workers run ``init.sh`` first and read ``feature-state.md`` for orientation. If the
agent fails to produce either artifact, Foreman writes a minimal deterministic
fallback so the build can still proceed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def build_prompt(
    *,
    slug: str,
    request: str,
    commands: dict[str, Optional[str]],
    init_path: Path,
    feature_state_path: Path,
) -> str:
    cmd_lines = "\n".join(f"  {n}: {c or '(not configured)'}" for n, c in commands.items())
    return (
        "You are running headless as a one-time feature initializer. Do NOT implement "
        "any feature work. Your job is to make the repo ready for a series of worker "
        "sessions and to leave them a concise orientation.\n\n"
        "Do these three things and then stop:\n"
        f"1. Write a bootstrap script to EXACTLY this path: {init_path}\n"
        "   It must be an idempotent shell script any fresh session can run first to "
        "   prepare the environment (install deps, activate venv, set env vars, etc.). "
        "   Keep it fast and safe to re-run.\n"
        "2. Confirm the project's commands actually run (fix the script if not):\n"
        f"{cmd_lines}\n"
        f"3. Write a concise orientation to EXACTLY this path: {feature_state_path}\n"
        "   Sections: ## Status (what this feature is, where the build stands), "
        "## Conventions (a SHORT digest of the repo's conventions from CONTEXT.md / "
        "neighbouring code — not the whole doc), and ## Gotchas (sharp edges a worker "
        "must know). Keep it under ~40 lines.\n\n"
        f"--- FEATURE REQUEST ---\n{request}\n"
    )


def _minimal_init_sh(commands: dict[str, Optional[str]]) -> str:
    test = (commands.get("test") or "").strip()
    return (
        "#!/usr/bin/env bash\n"
        "# Foreman feature bootstrap (minimal fallback). Idempotent; safe to re-run.\n"
        "set -e\n"
        + (f'# project test command: {test}\n' if test else "")
        + "exit 0\n"
    )


def _seed_feature_state(slug: str, request: str, commands: dict[str, Optional[str]]) -> str:
    cmd_lines = "\n".join(f"- {n}: `{c}`" for n, c in commands.items() if c)
    return (
        f"# Feature state — {slug}\n\n"
        "## Status\n"
        f"{request.strip() or '(no request text)'}\n\n"
        "## Commands\n"
        f"{cmd_lines or '- (none configured)'}\n\n"
        "## Conventions\n"
        "_(no digest yet — workers should consult CONTEXT.md and neighbouring code.)_\n\n"
        "## Gotchas\n"
        "_(none recorded yet.)_\n"
    )


def validate_and_fallback(
    *,
    slug: str,
    request: str,
    commands: dict[str, Optional[str]],
    init_path: Path,
    feature_state_path: Path,
) -> dict[str, bool]:
    """Ensure both artifacts exist; write minimal fallbacks for any the agent missed.

    Returns a flag dict noting which were written by fallback.
    """
    fell_back = {"init_sh": False, "feature_state": False}
    if not init_path.exists() or not init_path.read_text().strip():
        init_path.parent.mkdir(parents=True, exist_ok=True)
        init_path.write_text(_minimal_init_sh(commands))
        fell_back["init_sh"] = True
    if not feature_state_path.exists() or not feature_state_path.read_text().strip():
        feature_state_path.parent.mkdir(parents=True, exist_ok=True)
        feature_state_path.write_text(_seed_feature_state(slug, request, commands))
        fell_back["feature_state"] = True
    return fell_back


def read_feature_state(path: Path) -> str:
    return path.read_text() if path.exists() else ""
