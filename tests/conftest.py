from __future__ import annotations

import pytest

from novaforge.config import invalidate_settings_cache


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch):
    # Keep tests deterministic and headless-safe.
    # monkeypatch.setenv automatically restores env vars after each test.
    invalidate_settings_cache()
    monkeypatch.setenv("NOVAFORGE_ENABLE_ACTIVITY_GLOW", "0")
    monkeypatch.setenv("NOVAFORGE_REQUIRE_ALLOWLIST", "0")
    monkeypatch.setenv("NOVAFORGE_MAX_ACTIONS", "200")
    yield
    invalidate_settings_cache()
