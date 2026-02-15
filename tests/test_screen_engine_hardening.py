from __future__ import annotations

from pathlib import Path

from descon.engines.screen_engine import SCREEN_ENGINE


def test_resolve_monitor_clamps_out_of_range_indices():
    m_low = SCREEN_ENGINE._resolve_monitor(-99)
    m_high = SCREEN_ENGINE._resolve_monitor(10_000)
    assert isinstance(m_low, dict)
    assert isinstance(m_high, dict)
    assert m_low["width"] > 0 and m_low["height"] > 0
    assert m_high["width"] > 0 and m_high["height"] > 0


def test_find_template_unreadable_returns_empty_not_exception():
    data = SCREEN_ENGINE.find_template(template_path="__definitely_missing_template__.png")
    assert data["count"] == 0
    assert data["best"] is None


def test_capture_creates_missing_parent_directory(monkeypatch, tmp_path: Path):
    class _Shot:
        width = 2
        height = 2
        size = (2, 2)
        bgra = bytes([0, 0, 0, 255] * 4)

    class _Sct:
        monitors = [{"left": 0, "top": 0, "width": 2, "height": 2}]

        def grab(self, _mon):
            return _Shot()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    import descon.engines.screen_engine as se_mod
    monkeypatch.setattr(se_mod, "mss", lambda: _Sct())
    out_path = tmp_path / "nested" / "screens" / "shot.png"
    assert not out_path.parent.exists()

    data = SCREEN_ENGINE.capture(path=str(out_path), monitor_index=0)
    assert out_path.exists()
    assert data["path"] == str(out_path)
