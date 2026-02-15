from __future__ import annotations

import pytest

from descon.engines.window_engine import WINDOW_ENGINE


class _FakeWindow:
    def __init__(self):
        self.visible = False
        self.focus_calls = 0

    def is_visible(self):
        return self.visible

    def restore(self):
        self.visible = True

    def maximize(self):
        self.visible = True

    def set_focus(self):
        self.focus_calls += 1

    def process_id(self):
        return 123


def test_ensure_window_ready_revives_hidden_window(monkeypatch):
    w = _FakeWindow()
    monkeypatch.setattr(WINDOW_ENGINE, "_process_elevation", lambda pid: "standard")
    monkeypatch.setattr(WINDOW_ENGINE, "_is_admin", lambda: True)
    WINDOW_ENGINE._ensure_window_ready(w, timeout_ms=100, ensure_focus=True)
    assert w.visible is True
    assert w.focus_calls >= 1


def test_ensure_window_ready_raises_on_privilege_mismatch(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_STRICT_PRIVILEGE_CHECK", "1")
    w = _FakeWindow()
    w.visible = True
    monkeypatch.setattr(WINDOW_ENGINE, "_process_elevation", lambda pid: "elevated")
    monkeypatch.setattr(WINDOW_ENGINE, "_is_admin", lambda: False)
    with pytest.raises(RuntimeError, match="Privilege mismatch"):
        WINDOW_ENGINE._ensure_window_ready(w, timeout_ms=50, ensure_focus=True)


def test_resolve_window_falls_back_to_foreground_when_enabled(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_WINDOW_RESOLVE_FALLBACK_FOREGROUND", "1")
    sentinel = object()
    monkeypatch.setattr(WINDOW_ENGINE, "_window_from_title_regex", lambda title_regex: (_ for _ in ()).throw(RuntimeError("no match")))
    monkeypatch.setattr(WINDOW_ENGINE, "_window_from_foreground", lambda: sentinel)
    out = WINDOW_ENGINE.resolve_window(title_regex=".*does-not-exist.*", timeout_ms=10)
    assert out is sentinel
