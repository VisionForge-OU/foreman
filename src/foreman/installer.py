"""`foreman init` — scaffold ``.foreman/`` and install vendored skills (§5, §12)."""

from __future__ import annotations

from pathlib import Path

from . import vendored
from .agents import installer as agents_installer
from .paths import RepoPaths

CONFIG_TEMPLATE = """\
# Foreman per-target-repo configuration. Edit to match your project.
model_planner: claude-fable-5
model_worker: claude-fable-5
# The read-only evaluator grades on a cheaper model by default (WS2).
model_evaluator: claude-haiku-4-5-20251001
effort: high

required_skills:
  - foreman-grill-docs
  - foreman-to-prd
  - foreman-to-issues
  - foreman-tdd
  - foreman-plan
  - foreman-web-testing

required_agents:
  - foreman-evaluator
  - foreman-auditor
  - foreman-code-review
  - foreman-security-review

# Commands Foreman runs ITSELF to verify a worker's claim. Set to null to skip.
commands:
  test: "{test}"
  lint: "{lint}"
  typecheck: "{typecheck}"
  e2e: "{e2e}"

git:
  integration_branch: {integration_branch}
  merge_strategy: merge
  open_pr: false

limits:
  max_parallel: 2
  max_retries: 3
  daily_cost_usd: 50

run_budget:
  max_turns: 80
  max_cost_usd: 5.00
  timeout_min: 45

# The read-only evaluator (builder never grades its own work, WS2).
evaluator_enabled: true
evaluator_min_score: 3       # a rubric score below this is treated as objections
evaluator_budget:
  max_turns: 30
  max_cost_usd: 2.00
  timeout_min: 20

# The read-only spec-integrity auditor + review notifications (WS5).
auditor_enabled: true
model_auditor: claude-haiku-4-5-20251001
# notify_command: "ntfy publish my-topic"   # fired on review-needed / escalation

# WS7: two extra read-only merge-gate graders that review the committed slice after
# the evaluator passes. A blocking verdict bounces a fresh builder with the findings;
# repeated objections (or a hit on the retry ceiling) escalate to a human.
# Both are opt-in: flip to true to add the gate. (Cheap on the small model, but each
# adds a fresh agent run per issue and can bounce work back to the builder.)
code_review_enabled: false
model_code_reviewer: claude-haiku-4-5-20251001
code_review_budget:
  max_turns: 30
  max_cost_usd: 2.00
  timeout_min: 20
security_review_enabled: false
model_security_reviewer: claude-haiku-4-5-20251001
security_review_budget:
  max_turns: 30
  max_cost_usd: 2.00
  timeout_min: 20

# Eval flywheel: `foreman bench` settings (WS6).
bench_eval_set: .foreman/eval_set
bench_cost_ceiling_usd: 5.0

stuck_turns: 12
e2e_enabled: true
permission_mode: acceptEdits

# WS3.3: retries spawn FRESH sessions with a distilled failure report (never resume
# a failed context). Set to `resume` to continue the prior session instead.
retry_strategy: fresh

# WS4.3: run a specialist janitor pass (dedup → conventions → docs) after every N
# merged feature issues, each gated by the same verification pipeline.
janitor_enabled: true
janitor_every: 3
janitor_kinds: [dedup, conventions, docs]
"""


def _tool_available(command: str, root: Path) -> bool:
    """Is the command actually runnable here? Guards against guessing a tool the
    project doesn't have (e.g. ``mypy`` on a project that never installed it),
    which would otherwise make Foreman's own verify step fail every merge."""
    import shutil

    parts = command.split()
    if not parts:
        return False
    exe = parts[0]
    if shutil.which(exe) is None:
        return False
    # `npm|yarn|pnpm run <script>`: only keep it if the script is actually declared.
    if exe in ("npm", "yarn", "pnpm") and len(parts) >= 3 and parts[1] == "run":
        import json

        try:
            scripts = (json.loads((root / "package.json").read_text()).get("scripts") or {})
        except (OSError, ValueError):
            return False
        return parts[2] in scripts
    return True


def _detect_commands(repo_root: Path) -> dict[str, str]:
    """Best-effort guess of test/lint/typecheck/e2e for the project's stack.

    Only commands whose underlying tool is actually installed are kept; the rest
    are left blank for the user to fill in, so a guessed-but-absent tool never
    silently blocks every merge.
    """
    root = Path(repo_root)
    if (root / "package.json").exists():
        guess = {
            "test": "npm test", "lint": "npm run lint",
            "typecheck": "npm run typecheck", "e2e": "npx playwright test",
        }
    elif (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        guess = {
            "test": "pytest", "lint": "ruff check .",
            "typecheck": "mypy .", "e2e": "pytest -m e2e",
        }
    elif (root / "go.mod").exists():
        guess = {"test": "go test ./...", "lint": "go vet ./...", "typecheck": "", "e2e": ""}
    elif (root / "Cargo.toml").exists():
        guess = {"test": "cargo test", "lint": "cargo clippy",
                 "typecheck": "cargo check", "e2e": ""}
    else:
        guess = {"test": "", "lint": "", "typecheck": "", "e2e": ""}
    return {k: (v if (v and _tool_available(v, root)) else "") for k, v in guess.items()}


def _detect_integration_branch(repo_root: Path) -> str:
    head = Path(repo_root) / ".git" / "HEAD"
    if head.exists():
        text = head.read_text().strip()
        if text.startswith("ref: refs/heads/"):
            return text.split("refs/heads/", 1)[1]
    return "main"


def init_repo(repo_root: Path | str, *, force: bool = False) -> dict:
    """Scaffold the ``.foreman/`` tree and install vendored skills.

    Idempotent: re-running never destroys existing config or features; it only
    fills in what's missing and updates the vendored skills.
    """
    root = Path(repo_root).resolve()
    paths = RepoPaths(root)

    paths.foreman_dir.mkdir(parents=True, exist_ok=True)
    paths.features_dir.mkdir(parents=True, exist_ok=True)

    created_config = False
    if not paths.config_file.exists() or force:
        cmds = _detect_commands(root)
        paths.config_file.write_text(
            CONFIG_TEMPLATE.format(
                integration_branch=_detect_integration_branch(root), **cmds
            )
        )
        created_config = True

    installed = vendored.install(root, force=force)
    agents_installed = agents_installer.install(root, force=force)

    return {
        "root": str(root),
        "config_created": created_config,
        "skills_installed": installed,
        "skills_status": vendored.status(root),
        "agents_installed": agents_installed,
        "agents_status": agents_installer.status(root),
    }
