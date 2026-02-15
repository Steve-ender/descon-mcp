from __future__ import annotations

import ctypes
import time
from typing import Any

import pyautogui

pyautogui.PAUSE = 0.02

from descon.config import load_settings


def _clipboard_get() -> str:
    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not user32.OpenClipboard(0):
        return ""
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def _clipboard_set(text: str) -> None:
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    encoded = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(0):
        raise RuntimeError("Cannot open clipboard")
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h:
            raise RuntimeError("GlobalAlloc failed")
        try:
            p = kernel32.GlobalLock(h)
            if not p:
                raise RuntimeError("GlobalLock failed")
            ctypes.memmove(p, encoded, len(encoded))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                raise RuntimeError("SetClipboardData failed")
        except Exception:
            kernel32.GlobalFree(h)
            raise
    finally:
        user32.CloseClipboard()


class InputEngine:
    def __init__(self) -> None:
        self.refresh_policy()

    def refresh_policy(self) -> None:
        try:
            pyautogui.FAILSAFE = bool(load_settings().input_failsafe)
        except Exception:
            pyautogui.FAILSAFE = False

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        self.refresh_policy()
        pyautogui.click(x=x, y=y, button=button, clicks=clicks)
        return {"x": x, "y": y, "button": button, "clicks": clicks}

    def move(self, x: int, y: int, duration_ms: int = 0) -> dict[str, Any]:
        self.refresh_policy()
        duration = max(duration_ms, 0) / 1000.0
        pyautogui.moveTo(x, y, duration=duration)
        return {"x": x, "y": y, "duration_ms": duration_ms}

    def position(self) -> dict[str, int]:
        self.refresh_policy()
        x, y = pyautogui.position()
        return {"x": int(x), "y": int(y)}

    def type_text(self, text: str, interval_ms: int = 0) -> dict[str, Any]:
        self.refresh_policy()
        if text and text.isascii():
            pyautogui.write(text, interval=max(interval_ms, 0) / 1000.0)
            return {"typed_chars": len(text), "method": "write"}
        old_clipboard = ""
        try:
            old_clipboard = _clipboard_get()
        except Exception:
            pass
        try:
            _clipboard_set(text)
            pyautogui.hotkey("ctrl", "v")
        finally:
            try:
                _clipboard_set(old_clipboard)
            except Exception:
                pass
        return {"typed_chars": len(text), "method": "clipboard_paste"}

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        self.refresh_policy()
        pyautogui.hotkey(*keys)
        return {"keys": keys}

    def key_press(self, key: str, presses: int = 1, interval_ms: int = 0) -> dict[str, Any]:
        self.refresh_policy()
        pyautogui.press(key, presses=presses, interval=max(interval_ms, 0) / 1000.0)
        return {"key": key, "presses": presses}

    def mouse_down(self, x: int | None = None, y: int | None = None, button: str = "left") -> dict[str, Any]:
        self.refresh_policy()
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y))
        pyautogui.mouseDown(button=button)
        pos = pyautogui.position()
        return {"x": int(pos.x), "y": int(pos.y), "button": button}

    def mouse_up(self, x: int | None = None, y: int | None = None, button: str = "left") -> dict[str, Any]:
        self.refresh_policy()
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y))
        pyautogui.mouseUp(button=button)
        pos = pyautogui.position()
        return {"x": int(pos.x), "y": int(pos.y), "button": button}

    def scroll(self, clicks: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        self.refresh_policy()
        if x is not None and y is not None:
            pyautogui.scroll(clicks, x=int(x), y=int(y))
        else:
            pyautogui.scroll(clicks)
        pos = pyautogui.position()
        return {"clicks": clicks, "x": int(pos.x), "y": int(pos.y)}

    def hscroll(self, clicks: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        self.refresh_policy()
        if x is not None and y is not None:
            pyautogui.hscroll(clicks, x=int(x), y=int(y))
        else:
            pyautogui.hscroll(clicks)
        pos = pyautogui.position()
        return {"clicks": clicks, "x": int(pos.x), "y": int(pos.y), "direction": "horizontal"}

    def drag_to(
        self,
        x: int,
        y: int,
        duration_ms: int = 200,
        button: str = "left",
        mouse_down_up: bool = True,
    ) -> dict[str, Any]:
        self.refresh_policy()
        duration = max(0, int(duration_ms)) / 1000.0
        pyautogui.dragTo(int(x), int(y), duration=duration, button=button, mouseDownUp=mouse_down_up)
        return {
            "x": int(x),
            "y": int(y),
            "duration_ms": int(duration_ms),
            "button": button,
            "mouse_down_up": bool(mouse_down_up),
        }

    def clipboard_read(self) -> dict[str, Any]:
        text = _clipboard_get()
        return {"text": text, "chars": len(text)}

    def clipboard_write(self, text: str) -> dict[str, Any]:
        _clipboard_set(text)
        return {"chars": len(text)}

    def sleep(self, time_ms: int) -> dict[str, Any]:
        time.sleep(max(time_ms, 0) / 1000.0)
        return {"slept_ms": max(time_ms, 0)}


INPUT_ENGINE = InputEngine()
