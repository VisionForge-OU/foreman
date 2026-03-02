import pytest

from foreman import config
from foreman.config import Config, ConfigError


def test_load_sample_config(tmp_path):
    src = (tmp_path / "config.yaml")
    src.write_text(
        "model_planner: claude-fable-5\n"
        "effort: high\n"
        "permission_mode: acceptEdits\n"
        "commands:\n  test: pytest\n  lint: null\n"
        "limits:\n  max_parallel: 3\n"
    )
    cfg = config.load(src)
    assert cfg.effort == "high"
    assert cfg.command("test") == "pytest"
    assert cfg.command("lint") is None       # null -> skipped
    assert cfg.command("typecheck") is None  # absent -> skipped
    assert cfg.limits.max_parallel == 3


def test_invalid_effort_rejected():
    cfg = Config(effort="turbo")
    with pytest.raises(ConfigError):
        cfg.validate()


def test_invalid_permission_mode_rejected():
    cfg = Config(permission_mode="yolo")
    with pytest.raises(ConfigError):
        cfg.validate()


def test_defaults_valid():
    Config().validate()  # should not raise


def test_roundtrip_dict():
    cfg = Config()
    again = config.from_dict(cfg.to_dict())
    assert again.to_dict() == cfg.to_dict()
