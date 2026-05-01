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


def test_gate_review_defaults():
    # WS7: both extra gate graders are opt-in (fully wired, off by default).
    cfg = Config()
    assert cfg.code_review_enabled is False
    assert cfg.security_review_enabled is False
    assert cfg.model_code_reviewer
    assert cfg.model_security_reviewer
    assert cfg.code_review_budget.max_turns >= 1
    assert cfg.security_review_budget.max_turns >= 1


def test_gate_review_flags_load_from_yaml(tmp_path):
    src = tmp_path / "config.yaml"
    src.write_text(
        "code_review_enabled: false\n"
        "security_review_enabled: true\n"
        "model_security_reviewer: claude-opus-4-8\n"
        "security_review_budget:\n  max_turns: 12\n  max_cost_usd: 1.5\n  timeout_min: 10\n"
    )
    cfg = config.load(src)
    assert cfg.code_review_enabled is False
    assert cfg.security_review_enabled is True
    assert cfg.model_security_reviewer == "claude-opus-4-8"
    assert cfg.security_review_budget.max_turns == 12
