from foreman import vendored, config
from foreman.installer import init_repo, _detect_commands, _tool_available
from foreman.paths import RepoPaths
from foreman.vendored import SkillState


REQUIRED = ["foreman-grill-docs", "foreman-to-prd", "foreman-to-issues", "foreman-tdd"]


def test_tool_available_guards_absent_tools(tmp_path):
    assert _tool_available("", tmp_path) is False
    assert _tool_available("definitely-not-a-real-tool-xyz --flag", tmp_path) is False
    # An interpreter that is always present resolves true.
    import sys
    from pathlib import Path
    assert _tool_available(f"{Path(sys.executable).name} --version", tmp_path) is True


def test_detect_commands_drops_uninstalled_tools(tmp_path):
    """A python project never gets a `mypy .` typecheck guess unless mypy exists."""
    import shutil
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    cmds = _detect_commands(tmp_path)
    if shutil.which("mypy") is None:
        assert cmds["typecheck"] == ""        # not blindly "mypy ."
    if shutil.which("pytest") is None:
        assert cmds["test"] == ""
    # Whatever survives must be a tool that actually resolves on PATH.
    for v in cmds.values():
        if v:
            assert shutil.which(v.split()[0]) is not None


def test_packaged_skills_present():
    pkg = vendored.packaged_skills()
    for name in REQUIRED:
        assert name in pkg, f"{name} not packaged"
        assert pkg[name] >= 1


def test_init_scaffolds_and_installs(tmp_path):
    result = init_repo(tmp_path)
    paths = RepoPaths(tmp_path)
    assert paths.foreman_dir.is_dir()
    assert paths.features_dir.is_dir()
    assert paths.config_file.exists()
    assert result["config_created"] is True

    # All required skills installed into .claude/skills/.
    for name in REQUIRED:
        skill_md = paths.skills_install_dir / name / "SKILL.md"
        assert skill_md.exists(), f"{name} not installed"

    # Config loads and validates.
    cfg = config.load(paths.config_file)
    cfg.validate()

    # Status is all OK right after install.
    states = {s.name: s.state for s in vendored.status(tmp_path)}
    for name in REQUIRED:
        assert states[name] == SkillState.OK

    # No required skills missing.
    assert vendored.missing_required(tmp_path, REQUIRED) == []


def test_init_is_idempotent_and_preserves_config(tmp_path):
    init_repo(tmp_path)
    paths = RepoPaths(tmp_path)
    # User edits config.
    text = paths.config_file.read_text().replace("max_parallel: 2", "max_parallel: 9")
    paths.config_file.write_text(text)

    result = init_repo(tmp_path)  # re-run
    assert result["config_created"] is False
    assert "max_parallel: 9" in paths.config_file.read_text()


def test_outdated_skill_is_detected_and_updated(tmp_path):
    init_repo(tmp_path)
    paths = RepoPaths(tmp_path)
    # Simulate an old install by downgrading the version marker on disk.
    import re
    skill_md = paths.skills_install_dir / "foreman-tdd" / "SKILL.md"
    text = re.sub(r"foreman_skill_version: \d+", "foreman_skill_version: 0",
                  skill_md.read_text())
    skill_md.write_text(text)

    states = {s.name: s.state for s in vendored.status(tmp_path)}
    assert states["foreman-tdd"] == SkillState.OUTDATED

    written = vendored.install(tmp_path)
    assert "foreman-tdd" in written  # update applied
    states = {s.name: s.state for s in vendored.status(tmp_path)}
    assert states["foreman-tdd"] == SkillState.OK


def test_install_never_clobbers_non_foreman_skills(tmp_path):
    paths = RepoPaths(tmp_path)
    upstream = paths.skills_install_dir / "grill-with-docs"
    upstream.mkdir(parents=True)
    (upstream / "SKILL.md").write_text("---\nname: grill-with-docs\n---\nUSER COPY")

    init_repo(tmp_path)
    # The user's upstream skill is untouched.
    assert (upstream / "SKILL.md").read_text() == "---\nname: grill-with-docs\n---\nUSER COPY"
