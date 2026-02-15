from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, Callable

import pytest

import descon.tools.action_tools as action_tools
import descon.tools.file_tools as file_tools
import descon.tools.health_tools as health_tools
import descon.tools.input_tools as input_tools
import descon.tools.notify_tools as notify_tools
import descon.tools.inspect_tools as inspect_tools
import descon.tools.orchestration_tools as orchestration_tools
import descon.tools.recording_tools as recording_tools
import descon.tools.session_tools as session_tools
import descon.tools.shell_tools as shell_tools
import descon.tools.snapshot_tools as snapshot_tools
import descon.tools.transaction_tools as transaction_tools
import descon.tools.vision_tools as vision_tools
import descon.tools.wait_tools as wait_tools
import descon.tools.window_tools as window_tools
from descon.state import STATE


# --- Core server tools (15 tools) ---
CORE_REGISTER_FNS: list[Callable[[Any], None]] = [
    window_tools.register_window_tools,
    input_tools.register_input_tools,
    vision_tools.register_vision_tools,
    inspect_tools.register_inspect_tools,
    action_tools.register_action_tools,
    snapshot_tools.register_snapshot_tools,
    wait_tools.register_wait_tools,
    shell_tools.register_shell_tools,
    file_tools.register_file_tools,
    notify_tools.register_notify_tools,
    orchestration_tools.register_orchestration_tools,
]

# --- Admin server tools ---
ADMIN_REGISTER_FNS: list[Callable[[Any], None]] = [
    session_tools.register_session_tools,
    recording_tools.register_recording_tools,
    transaction_tools.register_transaction_tools,
    health_tools.register_health_tools,
    vision_tools.register_vision_admin_tools,
]

ALL_REGISTER_FNS = CORE_REGISTER_FNS + ADMIN_REGISTER_FNS

MODULES_TO_PATCH = [
    session_tools,
    window_tools,
    input_tools,
    vision_tools,
    inspect_tools,
    action_tools,
    orchestration_tools,
    recording_tools,
    transaction_tools,
    health_tools,
    snapshot_tools,
    wait_tools,
    shell_tools,
    file_tools,
    notify_tools,
]


class FakeMCP:
    def __init__(self):
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _patch_runtime(monkeypatch: pytest.MonkeyPatch):
    for mod in MODULES_TO_PATCH:
        if hasattr(mod, "assert_can_run"):
            monkeypatch.setattr(mod, "assert_can_run", lambda settings, session_id=None: "default")

    # Generic engine stubs
    monkeypatch.setattr(window_tools.WINDOW_ENGINE, "list_windows", lambda only_visible=True: [{"title": "Win", "handle": 1, "pid": 2}])
    monkeypatch.setattr(window_tools.WINDOW_ENGINE, "focus_window", lambda title_regex, timeout_ms=5000: {"title": "Win", "handle": 1, "pid": 2})
    monkeypatch.setattr(window_tools.WINDOW_ENGINE, "focus_window_by_pid", lambda pid, timeout_ms=5000: {"title": "Win", "handle": 1, "pid": int(pid)})
    monkeypatch.setattr(window_tools.WINDOW_ENGINE, "focus_window_by_handle", lambda window_handle, timeout_ms=4000: {"title": "Win", "handle": int(window_handle), "pid": 2})
    monkeypatch.setattr(
        window_tools.WINDOW_ENGINE,
        "resolve_window",
        lambda **kwargs: type(
            "W",
            (),
            {
                "window_text": lambda self: "Win",
                "handle": 1,
                "process_id": lambda self: 2,
                "rectangle": lambda self: type("R", (), {"left": 100, "top": 200, "right": 500, "bottom": 600})(),
            },
        )(),
    )
    monkeypatch.setattr(window_tools.WINDOW_ENGINE, "launch_app", lambda **kwargs: {"pid": 111, "command": kwargs.get("command", "")})
    monkeypatch.setattr(window_tools.WINDOW_ENGINE, "close_app", lambda **kwargs: {"closed": [{"pid": 111, "name": "mock"}]})
    monkeypatch.setattr(
        window_tools.WINDOW_ENGINE,
        "find_elements",
        lambda **kwargs: [{"title": "x", "control_type": "Button", "window_handle": 1, "rect": {"left": 0, "top": 0, "right": 10, "bottom": 10, "center_x": 5, "center_y": 5}}],
    )
    monkeypatch.setattr(window_tools.WINDOW_ENGINE, "get_foreground_window", lambda: {"handle": 1, "pid": 2, "thread_id": 3, "title": "Win", "class_name": "X"})
    monkeypatch.setattr(window_tools.WINDOW_ENGINE, "ensure_foreground_target", lambda **kwargs: {"ok": True, "matched": True, "mode": kwargs.get("mismatch_mode", "warn")})
    monkeypatch.setattr(
        window_tools.WINDOW_ENGINE,
        "element_tree",
        lambda **kwargs: {"window_title": "Win", "window_handle": 1, "window_pid": 2, "max_depth": 3, "tree": {"title": "Win", "control_type": "Window", "automation_id": "", "rect": {"left": 0, "top": 0, "right": 100, "bottom": 100, "center_x": 50, "center_y": 50}, "children": []}},
    )

    monkeypatch.setattr(action_tools.WINDOW_ENGINE, "snapshot_element", lambda **kwargs: {"visible": True, "enabled": True, "focused": True, "text_candidates": ["hello world"], "title": "hello world"})
    monkeypatch.setattr(action_tools.WINDOW_ENGINE, "perform_element_action", lambda **kwargs: {"strategy": "mock"})
    monkeypatch.setattr(action_tools.SCREEN_ENGINE, "find_template", lambda **kwargs: {"count": 1, "best": {"center_x": 10, "center_y": 10}, "matches": [{"center_x": 10, "center_y": 10}]})
    monkeypatch.setattr(action_tools.SCREEN_ENGINE, "capture", lambda **kwargs: {"path": "x.png", "width": 100, "height": 100})
    monkeypatch.setattr(action_tools.OCR_ENGINE, "select_text_target_local_model", lambda **kwargs: {"count": 1, "best": {"center_x": 10, "center_y": 10}, "matches": [{"center_x": 10, "center_y": 10}]})
    monkeypatch.setattr(action_tools.INPUT_ENGINE, "click", lambda **kwargs: {"x": kwargs.get("x"), "y": kwargs.get("y")})
    monkeypatch.setattr(action_tools.INPUT_ENGINE, "type_text", lambda **kwargs: {"typed_chars": len(kwargs.get("text", ""))})
    monkeypatch.setattr(action_tools.INPUT_ENGINE, "sleep", lambda time_ms: {"slept_ms": time_ms})

    monkeypatch.setattr(input_tools.INPUT_ENGINE, "position", lambda: {"x": 0, "y": 0})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "move", lambda **kwargs: {"x": kwargs.get("x"), "y": kwargs.get("y")})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "click", lambda **kwargs: {"x": kwargs.get("x"), "y": kwargs.get("y")})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "type_text", lambda **kwargs: {"typed_chars": len(kwargs.get("text", ""))})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "hotkey", lambda keys: {"keys": keys})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "key_press", lambda **kwargs: {"key": kwargs.get("key"), "presses": kwargs.get("presses", 1)})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "mouse_down", lambda **kwargs: {"x": kwargs.get("x"), "y": kwargs.get("y"), "button": kwargs.get("button", "left")})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "mouse_up", lambda **kwargs: {"x": kwargs.get("x"), "y": kwargs.get("y"), "button": kwargs.get("button", "left")})
    monkeypatch.setattr(
        input_tools.INPUT_ENGINE,
        "drag_to",
        lambda **kwargs: {
            "x": kwargs.get("x"),
            "y": kwargs.get("y"),
            "duration_ms": kwargs.get("duration_ms", 200),
            "button": kwargs.get("button", "left"),
            "mouse_down_up": kwargs.get("mouse_down_up", True),
        },
    )
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "sleep", lambda time_ms: {"slept_ms": time_ms})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "scroll", lambda **kwargs: {"clicks": kwargs.get("clicks", 0)})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "hscroll", lambda **kwargs: {"clicks": kwargs.get("clicks", 0)})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "clipboard_read", lambda: {"text": "clipboard_content"})
    monkeypatch.setattr(input_tools.INPUT_ENGINE, "clipboard_write", lambda text: {"written": True})
    monkeypatch.setattr(input_tools.WINDOW_ENGINE, "ensure_foreground_target", lambda **kwargs: {"ok": True, "matched": True})
    monkeypatch.setattr(input_tools.SCREEN_ENGINE, "capture", lambda **kwargs: {"path": "x.png", "width": 100, "height": 100})
    monkeypatch.setattr(input_tools.OCR_ENGINE, "select_text_target_local_model", lambda **kwargs: {"count": 1, "best": {"center_x": 10, "center_y": 10}, "matches": [{"center_x": 10, "center_y": 10}]})

    monkeypatch.setattr(vision_tools.SCREEN_ENGINE, "monitors", lambda: [{"left": 0, "top": 0, "width": 100, "height": 100}])
    monkeypatch.setattr(vision_tools.SCREEN_ENGINE, "capture", lambda **kwargs: {"path": "x.png", "width": 100, "height": 100})
    monkeypatch.setattr(vision_tools.SCREEN_ENGINE, "dpi_scale", lambda: 1.0)
    monkeypatch.setattr(vision_tools.SCREEN_ENGINE, "find_template", lambda **kwargs: {"count": 1, "best": {"center_x": 10, "center_y": 10}, "matches": [{"center_x": 10, "center_y": 10}]})
    monkeypatch.setattr(vision_tools.OCR_ENGINE, "read_text_local_model", lambda **kwargs: {"text": "abc", "chars": 3, "backend": "local_model"})
    monkeypatch.setattr(vision_tools.OCR_ENGINE, "read_text_tesseract", lambda **kwargs: {"text": "abc", "chars": 3, "backend": "tesseract"})
    monkeypatch.setattr(vision_tools.INPUT_ENGINE, "click", lambda **kwargs: {"x": kwargs.get("x"), "y": kwargs.get("y")})
    monkeypatch.setattr(vision_tools.INPUT_ENGINE, "sleep", lambda time_ms: {"slept_ms": time_ms})

    # PIL mock for desktop_pixel
    class _FakePixelImage:
        def getpixel(self, xy):
            return (128, 64, 32)

    monkeypatch.setattr(vision_tools, "PILImage", type("M", (), {"open": staticmethod(lambda p: _FakePixelImage())})())

    # Inspect tools
    monkeypatch.setattr(inspect_tools.WINDOW_ENGINE, "list_windows", lambda only_visible=True: [{"title": "Win", "handle": 1, "pid": 2}])
    monkeypatch.setattr(inspect_tools.INPUT_ENGINE, "position", lambda: {"x": 0, "y": 0})
    monkeypatch.setattr(inspect_tools.TRANSACTION_ENGINE, "status", lambda **kwargs: {"transaction_active": False})
    monkeypatch.setattr(inspect_tools.SCREEN_ENGINE, "monitors", lambda: [{"left": 0, "top": 0, "width": 100, "height": 100}])
    monkeypatch.setattr(inspect_tools.SCREEN_ENGINE, "dpi_scale", lambda: 1.0)
    monkeypatch.setattr(inspect_tools.SCREEN_ENGINE, "capture", lambda **kwargs: {"path": "x.png", "width": 100, "height": 100})
    monkeypatch.setattr(inspect_tools.SCREEN_ENGINE, "annotate_screenshot", lambda image_path, elements: "annotated.png")
    monkeypatch.setattr(inspect_tools.OCR_ENGINE, "read_text_local_model", lambda image_path: {"text": "abc", "chars": 3, "backend": "local_model"})

    # UIA tree engine mock
    from descon.engines.uia_tree_engine import UIASnapshot
    _fake_snapshot = UIASnapshot(interactive=[], scrollable=[], dom_text=[], elapsed_ms=50)
    monkeypatch.setattr(inspect_tools.UIA_TREE_ENGINE, "snapshot", lambda *a, **kw: _fake_snapshot)

    # Snapshot tools
    monkeypatch.setattr(
        snapshot_tools.WINDOW_ENGINE,
        "element_tree",
        lambda **kwargs: {"window_title": "Win", "window_handle": 1, "window_pid": 2, "max_depth": 3, "tree": {"title": "Win", "control_type": "Window", "automation_id": "", "rect": {"left": 0, "top": 0, "right": 100, "bottom": 100, "center_x": 50, "center_y": 50}, "children": []}},
    )

    # Wait tools
    monkeypatch.setattr(wait_tools.INPUT_ENGINE, "sleep", lambda time_ms: {"slept_ms": time_ms})
    monkeypatch.setattr(wait_tools.SCREEN_ENGINE, "capture", lambda **kwargs: {"path": "x.png", "width": 100, "height": 100, "base64_png": "AAAA"})
    monkeypatch.setattr(wait_tools.OCR_ENGINE, "select_text_target_local_model", lambda **kwargs: {"count": 0, "best": None, "matches": []})
    monkeypatch.setattr(wait_tools.WINDOW_ENGINE, "list_windows", lambda only_visible=True: [{"title": "Win", "handle": 1, "pid": 2}])

    # Shell tools
    monkeypatch.setattr(
        shell_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})(),
    )

    # Notify tools
    monkeypatch.setattr(
        notify_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )

    monkeypatch.setattr(transaction_tools.TRANSACTION_ENGINE, "start", lambda **kwargs: {"transaction_id": "tx1"})
    monkeypatch.setattr(transaction_tools.TRANSACTION_ENGINE, "status", lambda **kwargs: {"transaction_active": False})
    monkeypatch.setattr(transaction_tools.TRANSACTION_ENGINE, "checkpoint", lambda **kwargs: {"index": 1, "path": "cp.png"})
    monkeypatch.setattr(transaction_tools.TRANSACTION_ENGINE, "rollback_hint", lambda **kwargs: {"hints": []})
    monkeypatch.setattr(transaction_tools.TRANSACTION_ENGINE, "end", lambda **kwargs: {"committed": kwargs.get("committed", True)})
    monkeypatch.setattr(transaction_tools.TRANSACTION_ENGINE, "export", lambda **kwargs: {"path": "export.json"})

    monkeypatch.setattr(recording_tools, "execute_plan", lambda **kwargs: {"ok": True, "action": "desktop_act", "data": {"succeeded": True}})

    monkeypatch.setattr(health_tools.SCREEN_ENGINE, "monitors", lambda: [{"left": 0, "top": 0, "width": 100, "height": 100}])
    monkeypatch.setattr(health_tools.WINDOW_ENGINE, "get_foreground_window", lambda: {"handle": 1, "pid": 2, "title": "Win", "class_name": "X", "thread_id": 3})
    monkeypatch.setattr(health_tools.WINDOW_ENGINE, "list_windows", lambda only_visible=True: [{"title": "Win", "handle": 1, "pid": 2}])
    monkeypatch.setattr(health_tools.ACTIVITY_INDICATOR, "status", lambda: {"enabled": False, "running": False, "last_error": None})


def _required_kwargs(fn: Callable[..., Any], tmp_path: Path) -> dict[str, Any]:
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if param.default is not inspect._empty:
            continue
        if name == "ctx":
            kwargs[name] = None
            continue
        if name in {"title_regex", "window_title_regex"}:
            kwargs[name] = ".*"
        elif name in {"window_handle", "pid", "window_pid"}:
            kwargs[name] = 1
        elif name in {"x", "y", "left", "top", "width", "height", "time_ms", "duration_ms", "timeout_ms", "poll_ms"}:
            kwargs[name] = 1
        elif name in {"clicks", "presses", "max_results", "max_attempts", "monitor_index", "found_index"}:
            kwargs[name] = 1
        elif name == "button":
            kwargs[name] = "left"
        elif name == "keys":
            kwargs[name] = ["ctrl", "c"]
        elif name == "key":
            kwargs[name] = "enter"
        elif name == "text":
            kwargs[name] = "hello"
        elif name == "command":
            kwargs[name] = "echo hello"
        elif name == "action":
            kwargs[name] = "click"
        elif name == "condition":
            kwargs[name] = "exists"
        elif name == "plan":
            kwargs[name] = [{"action": "wait", "time_ms": 1}]
        elif name == "intents":
            kwargs[name] = [{"intent": "wait", "time_ms": 1}]
        elif name == "base_plan":
            kwargs[name] = [{"action": "wait", "time_ms": 1}]
        elif name == "target_plan":
            kwargs[name] = [{"action": "wait", "time_ms": 2}]
        elif name == "path":
            rec_file = tmp_path / "recording.json"
            rec_file.write_text(json.dumps({"events": []}), encoding="utf-8")
            kwargs[name] = str(rec_file)
        elif name == "image_path":
            kwargs[name] = "dummy.png"
        elif name == "template_path":
            kwargs[name] = "dummy-template.png"
        elif name == "contains":
            kwargs[name] = "abc"
        elif name == "backend":
            kwargs[name] = "invalid"
        elif name == "mode":
            kwargs[name] = "reuse"
        else:
            kwargs[name] = "x"
    return kwargs


@pytest.mark.parametrize("register_fn", ALL_REGISTER_FNS)
def test_register_tools_contract_and_invoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, register_fn: Callable[[Any], None]):
    _patch_runtime(monkeypatch)
    fake = FakeMCP()
    register_fn(fake)
    assert fake.tools, f"No tools registered for {register_fn.__name__}"

    STATE.start()
    try:
        for name, fn in fake.tools.items():
            kwargs = _required_kwargs(fn, tmp_path=tmp_path)
            if inspect.iscoroutinefunction(fn):
                res = asyncio.run(fn(**kwargs))
            else:
                res = fn(**kwargs)
            if isinstance(res, list):
                res = res[0]
            assert isinstance(res, dict), f"{name} did not return dict"
            assert "ok" in res, f"{name} missing 'ok' key"
    finally:
        STATE.stop()


def test_session_policy_reflects_env_changes_after_registration(monkeypatch: pytest.MonkeyPatch):
    from descon.config import invalidate_settings_cache

    fake = FakeMCP()
    session_tools.register_session_tools(fake)
    tool = fake.tools["desktop_session"]

    invalidate_settings_cache()
    monkeypatch.setenv("DESCON_MAX_ACTIONS", "7")
    res1 = tool(action="policy")
    assert res1["ok"] is True
    assert res1["data"]["max_actions"] == 7

    invalidate_settings_cache()
    monkeypatch.setenv("DESCON_MAX_ACTIONS", "19")
    res2 = tool(action="policy")
    assert res2["ok"] is True
    assert res2["data"]["max_actions"] == 19
