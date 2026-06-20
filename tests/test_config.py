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


# --------------------------------------------------------------------------- #
# Model-aware turn budgets (issue #1)
# --------------------------------------------------------------------------- #
def test_turn_budget_defaults():
    cfg = Config()
    assert cfg.turn_budget_by_model == {}
    assert cfg.turn_tiers == {}
    assert cfg.phase_turn_factors == {}
    # wall + cost are the primary extension limits; the count is a backstop.
    assert cfg.extension_wall_min == 30
    assert cfg.extension_cost_usd == 3.0
    assert cfg.max_turn_extensions == 6


def test_turn_budget_fields_load_from_yaml(tmp_path):
    src = tmp_path / "config.yaml"
    src.write_text(
        "turn_budget_by_model:\n  claude-haiku-4-5: 80\n"
        "turn_tiers:\n  small: 70\n"
        "phase_turn_factors:\n  grill: 2.0\n"
        "extension_wall_min: 45\n"
        "extension_cost_usd: 4.5\n"
        "max_turn_extensions: 4\n"
    )
    cfg = config.load(src)
    assert cfg.turn_budget_by_model == {"claude-haiku-4-5": 80}
    assert cfg.turn_tiers == {"small": 70}
    assert cfg.phase_turn_factors == {"grill": 2.0}
    assert cfg.extension_wall_min == 45
    assert cfg.extension_cost_usd == 4.5
    assert cfg.max_turn_extensions == 4


def test_turn_budget_fields_roundtrip():
    cfg = Config(
        turn_budget_by_model={"claude-haiku-4-5": 80},
        turn_tiers={"small": 70, "large": 35},
        phase_turn_factors={"grill": 2.0},
        extension_wall_min=45,
        extension_cost_usd=4.5,
    )
    again = config.from_dict(cfg.to_dict())
    assert again.to_dict() == cfg.to_dict()
    assert again.turn_budget_by_model == {"claude-haiku-4-5": 80}
    assert again.turn_tiers == {"small": 70, "large": 35}
    assert again.phase_turn_factors == {"grill": 2.0}


def test_negative_extension_wall_rejected():
    with pytest.raises(ConfigError):
        Config(extension_wall_min=-1).validate()


def test_non_positive_extension_cost_rejected():
    with pytest.raises(ConfigError):
        Config(extension_cost_usd=0).validate()


def test_non_positive_tier_floor_rejected():
    with pytest.raises(ConfigError):
        Config(turn_tiers={"small": 0}).validate()


def test_non_positive_phase_factor_rejected():
    with pytest.raises(ConfigError):
        Config(phase_turn_factors={"grill": 0}).validate()


def test_non_positive_model_pin_rejected():
    with pytest.raises(ConfigError):
        Config(turn_budget_by_model={"claude-haiku-4-5": 0}).validate()
