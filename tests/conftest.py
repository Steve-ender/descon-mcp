from __future__ import annotations

import pytest

from descon.config import invalidate_settings_cache


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch):
    # Keep tests deterministic and headless-safe.
    # monkeypatch.setenv automatically restores env vars after each test.
    invalidate_settings_cache()
    monkeypatch.setenv("DESCON_ENABLE_ACTIVITY_GLOW", "0")
    monkeypatch.setenv("DESCON_REQUIRE_ALLOWLIST", "0")
    monkeypatch.setenv("DESCON_MAX_ACTIONS", "200")
    yield
    invalidate_settings_cache()
