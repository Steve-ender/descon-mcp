from __future__ import annotations

from pathlib import Path

from descon.config import invalidate_settings_cache, load_settings


def test_config_sanitizes_invalid_values(monkeypatch):
    invalidate_settings_cache()
    monkeypatch.setenv("NOVAFORGE_MAX_ACTIONS", "not-an-int")
    monkeypatch.setenv("NOVAFORGE_ACTIVITY_GLOW_THICKNESS", "-20")
    monkeypatch.setenv("NOVAFORGE_ACTIVITY_GLOW_COLOR", "not-a-color")
    settings = load_settings()
    assert settings.max_actions == 0
    assert settings.activity_glow_thickness == 2
    assert settings.activity_glow_color == "#00ff88"


def test_config_accepts_valid_values(monkeypatch):
    invalidate_settings_cache()
    monkeypatch.setenv("NOVAFORGE_MAX_ACTIONS", "1234")
    monkeypatch.setenv("NOVAFORGE_ACTIVITY_GLOW_THICKNESS", "22")
    monkeypatch.setenv("NOVAFORGE_ACTIVITY_GLOW_COLOR", "#112233")
    monkeypatch.setenv("NOVAFORGE_REQUIRE_ALLOWLIST", "1")
    monkeypatch.setenv("NOVAFORGE_ALLOWLIST", "notepad,powershell")
    settings = load_settings()
    assert settings.max_actions == 1234
    assert settings.activity_glow_thickness == 22
    assert settings.activity_glow_color == "#112233"
    assert settings.require_allowlist is True
    assert "notepad" in settings.allowlist


def test_config_defaults_enable_autostart_and_disable_failsafe(monkeypatch):
    invalidate_settings_cache()
    monkeypatch.delenv("NOVAFORGE_AUTO_START_SESSION", raising=False)
    monkeypatch.delenv("NOVAFORGE_INPUT_FAILSAFE", raising=False)
    settings = load_settings()
    assert settings.auto_start_session is True
    assert settings.input_failsafe is False


def test_config_artifacts_dir_from_env(monkeypatch, tmp_path: Path):
    invalidate_settings_cache()
    custom = tmp_path / "nf_artifacts"
    monkeypatch.setenv("NOVAFORGE_ARTIFACTS_DIR", str(custom))
    settings = load_settings()
    assert Path(settings.artifacts_dir) == custom
    assert custom.exists()


def test_config_broker_flags(monkeypatch):
    invalidate_settings_cache()
    monkeypatch.setenv("NOVAFORGE_ENABLE_ELEVATED_BROKER", "1")
    monkeypatch.setenv("NOVAFORGE_ELEVATED_BROKER_COMMAND", "python -m descon.elevated_broker_server")
    monkeypatch.setenv("NOVAFORGE_ELEVATED_BROKER_TIMEOUT_MS", "20000")
    monkeypatch.setenv("NOVAFORGE_BROKER_ROUTE_ON_PRIVILEGE_MISMATCH", "0")
    settings = load_settings()
    assert settings.enable_elevated_broker is True
    assert settings.elevated_broker_command == "python -m descon.elevated_broker_server"
    assert settings.elevated_broker_timeout_ms == 20000
    assert settings.broker_route_on_privilege_mismatch is False
