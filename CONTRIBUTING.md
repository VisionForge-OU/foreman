# Contributing to Foreman

## Versioning

Foreman follows **[Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH)**. The single source of truth is `version` in `pyproject.toml` — the README badge, changelog, and PyPI package metadata are all derived from it.

### Bump policy

| Change type | Version bump | Example |
|-------------|-------------|---------|
| Breaking change to the pipeline, CLI flags, or public config schema | **MAJOR** | Removing a `foreman.yaml` key, renaming the `foreman` CLI entry-point |
| New feature or opt-in behaviour (backwards-compatible) | **MINOR** | New gate agent, new `--flag`, new config knob |
| Bug fix, docs, or internal refactor with no user-visible change | **PATCH** | Crash fix, performance tweak, typo |

> **Pre-1.0 note:** while the project is < 1.0 the minor version acts as the major — breaking changes may land in a minor bump. The project will signal 1.0 when the pipeline API and config schema are stable.

### Releasing

1. Update `version` in `pyproject.toml`.
2. Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/).
3. Open a PR; merge to `main`.
4. Push a `vX.Y.Z` tag — CI will build, validate, publish to PyPI, and create a GitHub Release automatically.

Pre-releases use `vX.Y.ZaN` / `vX.Y.ZbN` / `vX.Y.ZrcN` — these publish to TestPyPI only.

## Development setup

```bash
uv sync --extra dev
uv run pytest
```

## Pull requests

- One logical change per PR.
- Tests must pass (`uv run pytest`).
- Add a `CHANGELOG.md` entry for any user-visible change.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`.
