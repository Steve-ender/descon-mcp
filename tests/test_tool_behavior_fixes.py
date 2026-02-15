from __future__ import annotations

import asyncio

import descon.tools.action_tools as action_tools
import descon.tools.vision_tools as vision_tools
import descon.tools.wait_tools as wait_tools
from descon.errors import ArtifactPathError
from descon.state import STATE


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def test_action_tool_ocr_fallback_uses_region_local_near_coords(monkeypatch):
    fake = _FakeMCP()
    action_tools.register_action_tools(fake)
    fn = fake.tools["desktop_element"]

    seen = {"near_x": None, "near_y": None}

    monkeypatch.setattr(action_tools, "assert_can_run", lambda settings, session_id=None: "default")
    monkeypatch.setattr(
        action_tools.WINDOW_ENGINE,
        "perform_element_action",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("uia failed")),
    )

    def _fake_click_ocr_text(**kwargs):
        seen["near_x"] = kwargs.get("near_x")
        seen["near_y"] = kwargs.get("near_y")
        return {"strategy": "ocr_text", "match": {"center_x": 10, "center_y": 20}}

    monkeypatch.setattr(action_tools, "_click_ocr_text", _fake_click_ocr_text)

    STATE.start()
    try:
        res = fn(
            action="act",
            element_action="click",
            text_query="Save",
            left=100,
            top=200,
            width=300,
            height=400,
            require_foreground_match=False,
        )
        assert res["ok"] is True
        assert seen["near_x"] == 150
        assert seen["near_y"] == 200
    finally:
        STATE.stop()


def test_action_wait_for_text_contains_requires_query(monkeypatch):
    fake = _FakeMCP()
    action_tools.register_action_tools(fake)
    fn = fake.tools["desktop_element"]
    monkeypatch.setattr(action_tools, "assert_can_run", lambda settings, session_id=None: "default")

    STATE.start()
    try:
        res = fn(action="wait_for", condition="text_contains", timeout_ms=10, poll_ms=10)
        assert res["ok"] is False
        assert res["error"]["code"] == "validation_error"
    finally:
        STATE.stop()


def test_wait_marks_action(monkeypatch):
    fake = _FakeMCP()
    wait_tools.register_wait_tools(fake)
    fn = fake.tools["desktop_wait"]
    monkeypatch.setattr(wait_tools, "assert_can_run", lambda settings, session_id=None: "default")
    monkeypatch.setattr(wait_tools.INPUT_ENGINE, "sleep", lambda time_ms: {"slept_ms": time_ms})

    STATE.start()
    try:
        before = STATE.get().action_count
        res = fn(time_ms=1)
        after = STATE.get().action_count
        assert res["ok"] is True
        assert after == before + 1
    finally:
        STATE.stop()


def test_vision_screenshot_maps_missing_path_error(monkeypatch):
    fake = _FakeMCP()
    vision_tools.register_vision_tools(fake)
    fn = fake.tools["desktop_screenshot"]
    monkeypatch.setattr(vision_tools, "assert_can_run", lambda settings, session_id=None: "default")
    monkeypatch.setattr(
        vision_tools.SCREEN_ENGINE,
        "capture",
        lambda **kwargs: (_ for _ in ()).throw(ArtifactPathError("Invalid path")),
    )

    STATE.start()
    try:
        res = fn(path="Z:\\missing\\dir\\shot.png")
        assert res["ok"] is False
        assert res["error"]["code"] == "path_missing"
    finally:
        STATE.stop()


def test_vision_read_text_reports_low_confidence_warning(monkeypatch):
    fake = _FakeMCP()
    vision_tools.register_vision_tools(fake)
    fn = fake.tools["desktop_read_text"]
    monkeypatch.setattr(vision_tools, "assert_can_run", lambda settings, session_id=None: "default")
    monkeypatch.setattr(
        vision_tools.OCR_ENGINE,
        "read_text_stable",
        lambda **kwargs: {
            "text": "Sove",
            "chars": 4,
            "backend": "local_model",
            "consensus_count": 1,
            "attempt_count": 3,
            "low_confidence": True,
            "errors": [],
        },
    )

    STATE.start()
    try:
        res = asyncio.run(fn(image_path="fake.png", backend="local_model"))
        assert res["ok"] is True
        assert res["data"]["warning"] == "low_confidence_text"
    finally:
        STATE.stop()
