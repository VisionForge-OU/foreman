# Repository Guidelines

## Project Structure & Module Organization

Foreman is a Python 3.11+ package using a `src/` layout. Core code lives in `src/foreman/`, with CLI entry points in `cli.py`, orchestration/state modules such as `pipeline.py`, `scheduler.py`, `runner.py`, and Textual UI code in `src/foreman/tui/`. Packaged runtime assets live in `src/foreman/skills/`, `src/foreman/agents/`, `src/foreman/hooks/assets/`, and `src/foreman/fixtures/`. Tests live in `tests/` and mirror module concerns with files like `test_state.py`, `test_pipeline.py`, and `test_tui.py`. `examples/hello/` is a sample target repo; `validation/` stores validation notes and evidence.

## Build, Test, and Development Commands

- `uv venv && uv pip install -e ".[dev]"`: create a local environment and install Foreman with pytest, pytest-asyncio, and Textual dev extras.
- `pytest -q`: run the full offline test suite.
- `python -m pytest tests/test_state.py::test_create_and_recover_feature -q`: run a focused test.
- `foreman demo`: exercise the pipeline against a throwaway sample repo with the mocked backend.
- `pipx install .` or `uv tool install .`: install the `foreman` console script locally.

## Coding Style & Naming Conventions

Use standard Python style: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, and uppercase constants. Prefer dataclasses/enums for domain state and typed signatures for public or cross-module APIs. Keep modules focused on one orchestration concern, and keep user-facing strings clear because they surface in the TUI and generated files. No formatter configuration is committed; follow surrounding style and group imports as standard library, third-party, then local.

## Testing Guidelines

The suite uses `pytest` and `pytest-asyncio` with `asyncio_mode = "auto"`. Name tests `test_<behavior>` and place them in `tests/test_<module>.py`. Use `tmp_path` and fixtures for filesystem state; prefer the mocked backend for agent behavior so tests remain offline and token-free. Add regression tests for changes to persistence, gates, scheduling, hooks, git worktree logic, and TUI controller behavior.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, often ending with a release marker, for example `Fix endless builder<->evaluator loop on pass-with-objections (0.4.3)`. Keep the first line specific and concise. PRs should explain the behavior change, list tests run, link related issues or validation notes, and include screenshots or terminal output for visible TUI changes.

## Security & Configuration Tips

Do not commit `.foreman/` run artifacts from target repos, credentials, Claude transcripts with secrets, or local virtual environments. Use `config.sample.yaml` as the configuration reference and keep default commands conservative.
