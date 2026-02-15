from __future__ import annotations

from fastmcp import FastMCP

from descon.config import load_settings
from descon.engines.input_engine import INPUT_ENGINE
from descon.engines.ocr_engine import OCR_ENGINE
from descon.engines.screen_engine import SCREEN_ENGINE
from descon.engines.window_engine import WINDOW_ENGINE
from descon.result import err, now_ms, ok
from descon.safety import assert_can_run
from descon.state import STATE
from descon.tools.recording_tools import maybe_record


def register_input_tools(mcp: FastMCP) -> None:
    def _effective_target(
        window_title_regex: str | None,
        window_handle: int | None,
        window_pid: int | None,
        session_id: str | None = None,
    ) -> tuple[str | None, int | None, int | None]:
        if window_handle is not None or window_pid is not None or (window_title_regex or "").strip():
            return window_title_regex, window_handle, window_pid
        st = STATE.get(session_id=session_id)
        return st.bound_window_title_regex, st.bound_window_handle, st.bound_window_pid

    def _guard_foreground(
        *,
        require_foreground_match: bool,
        window_title_regex: str | None,
        window_handle: int | None,
        window_pid: int | None,
        foreground_mismatch_mode: str,
        foreground_retries: int,
        foreground_retry_wait_ms: int,
        session_id: str | None = None,
    ) -> dict | None:
        if not require_foreground_match:
            return None
        target_regex, target_handle, target_pid = _effective_target(
            window_title_regex=window_title_regex,
            window_handle=window_handle,
            window_pid=window_pid,
            session_id=session_id,
        )
        return WINDOW_ENGINE.ensure_foreground_target(
            expected_handle=target_handle,
            expected_pid=target_pid,
            expected_title_regex=target_regex,
            mismatch_mode=foreground_mismatch_mode,
            retries=foreground_retries,
            retry_wait_ms=foreground_retry_wait_ms,
        )

    @mcp.tool(
        description=(
            "Click at screen coordinates OR find text on screen and click it. "
            "Coordinate mode: desktop_click(x=100, y=200). "
            "Text mode: desktop_click(text='Save') — finds text via OCR and clicks it. "
            "Use near_x/near_y to prefer a match closest to a point when the same text appears multiple times. "
            "Use relative_to='Window Title' to make (x, y) relative to a window's top-left corner. "
            "button: left/right/middle. clicks: 1 for single, 2 for double-click."
        ),
    )
    def desktop_click(
        x: int | None = None,
        y: int | None = None,
        text: str | None = None,
        button: str = "left",
        clicks: int = 1,
        monitor_index: int = 1,
        near_x: int | None = None,
        near_y: int | None = None,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        relative_to: str | None = None,
        require_foreground_match: bool = False,
        window_title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        foreground_mismatch_mode: str = "refocus_and_retry",
        foreground_retries: int = 3,
        foreground_retry_wait_ms: int = 150,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            fg = _guard_foreground(
                require_foreground_match=require_foreground_match,
                window_title_regex=window_title_regex,
                window_handle=window_handle,
                window_pid=window_pid,
                foreground_mismatch_mode=foreground_mismatch_mode,
                foreground_retries=foreground_retries,
                foreground_retry_wait_ms=foreground_retry_wait_ms,
                session_id=resolved_session_id,
            )

            if text is not None and text.strip():
                # OCR mode: find text on screen and click it
                region = None
                if None not in (left, top, width, height):
                    region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
                shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index, region=region)
                ranked = OCR_ENGINE.select_text_target_local_model(
                    image_path=str(shot["path"]),
                    query=text,
                    near_x=near_x,
                    near_y=near_y,
                )
                if ranked["count"] < 1 or not ranked.get("best"):
                    return err("desktop_click", f"Text not found on screen: {text}", started_ms=started, code="not_found")
                best = ranked["best"]
                offset_left = int(region["left"]) if region and "left" in region else 0
                offset_top = int(region["top"]) if region and "top" in region else 0
                click_x = int(best["center_x"] + offset_left)
                click_y = int(best["center_y"] + offset_top)
                data = INPUT_ENGINE.click(x=click_x, y=click_y, button=button, clicks=clicks)
                data["strategy"] = "ocr_text"
                data["match"] = best
                data["text_query"] = text
            elif x is not None and y is not None:
                # Coordinate mode: click at x, y (optionally relative to a window)
                click_x, click_y = int(x), int(y)
                relative_window_info = None
                if relative_to is not None and relative_to.strip():
                    try:
                        window = WINDOW_ENGINE.resolve_window(title_regex=relative_to.strip())
                        rect = window.rectangle()
                        relative_window_info = {
                            "title_regex": relative_to,
                            "window_title": window.window_text() if hasattr(window, "window_text") else "",
                            "rect": {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
                            "original_x": int(x),
                            "original_y": int(y),
                        }
                        click_x += rect.left
                        click_y += rect.top
                        relative_window_info["adjusted_x"] = click_x
                        relative_window_info["adjusted_y"] = click_y
                    except Exception as e:
                        return err(
                            "desktop_click",
                            f"Could not resolve window for relative_to='{relative_to}': {e}",
                            started_ms=started,
                            code="not_found",
                        )
                data = INPUT_ENGINE.click(x=click_x, y=click_y, button=button, clicks=clicks)
                if relative_window_info is not None:
                    data["relative_to"] = relative_window_info
            else:
                return err(
                    "desktop_click",
                    "Provide (x, y) coordinates OR text to find on screen",
                    started_ms=started,
                    code="validation_error",
                )

            if fg is not None:
                data["foreground"] = {"pre": fg}
            maybe_record("desktop_click", {"x": x, "y": y, "text": text, "button": button, "clicks": clicks}, data, session_id=resolved_session_id)
            return ok("desktop_click", data=data, started_ms=started)
        except Exception as e:
            return err("desktop_click", str(e), started_ms=started)

    @mcp.tool(description="Type text at the current cursor position. Supports Unicode text. Use interval_ms to slow down typing.")
    def desktop_type(
        text: str,
        interval_ms: int = 0,
        require_foreground_match: bool = False,
        window_title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        foreground_mismatch_mode: str = "refocus_and_retry",
        foreground_retries: int = 3,
        foreground_retry_wait_ms: int = 150,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            fg = _guard_foreground(
                require_foreground_match=require_foreground_match,
                window_title_regex=window_title_regex,
                window_handle=window_handle,
                window_pid=window_pid,
                foreground_mismatch_mode=foreground_mismatch_mode,
                foreground_retries=foreground_retries,
                foreground_retry_wait_ms=foreground_retry_wait_ms,
                session_id=resolved_session_id,
            )
            data = INPUT_ENGINE.type_text(text=text, interval_ms=interval_ms)
            if fg is not None:
                data["foreground"] = {"pre": fg}
            maybe_record("desktop_type", {"text": text, "interval_ms": interval_ms}, data, session_id=resolved_session_id)
            return ok("desktop_type", data=data, started_ms=started)
        except Exception as e:
            return err("desktop_type", str(e), started_ms=started)

    @mcp.tool(
        description=(
            "Scroll the mouse wheel at the current cursor position or at (x, y). "
            "Positive clicks = scroll up, negative = scroll down. "
            "Use direction='horizontal' for horizontal scrolling."
        ),
    )
    def desktop_scroll(
        clicks: int,
        x: int | None = None,
        y: int | None = None,
        direction: str = "vertical",
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            if direction.strip().lower() == "horizontal":
                data = INPUT_ENGINE.hscroll(clicks=clicks, x=x, y=y)
            else:
                data = INPUT_ENGINE.scroll(clicks=clicks, x=x, y=y)
            maybe_record("desktop_scroll", {"clicks": clicks, "x": x, "y": y, "direction": direction}, data, session_id=resolved_session_id)
            return ok("desktop_scroll", data=data, started_ms=started)
        except Exception as e:
            return err("desktop_scroll", str(e), started_ms=started)

    @mcp.tool(
        description=(
            "Keyboard input. For key combos (hotkeys), pass keys=['ctrl','c']. "
            "For single key presses, pass key='enter' (optionally presses=N, interval_ms=). "
            "Provide either keys (combo) or key (single), not both."
        ),
    )
    def desktop_key(
        keys: list[str] | None = None,
        key: str | None = None,
        presses: int = 1,
        interval_ms: int = 0,
        require_foreground_match: bool = False,
        window_title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        foreground_mismatch_mode: str = "refocus_and_retry",
        foreground_retries: int = 3,
        foreground_retry_wait_ms: int = 150,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            fg = _guard_foreground(
                require_foreground_match=require_foreground_match,
                window_title_regex=window_title_regex,
                window_handle=window_handle,
                window_pid=window_pid,
                foreground_mismatch_mode=foreground_mismatch_mode,
                foreground_retries=foreground_retries,
                foreground_retry_wait_ms=foreground_retry_wait_ms,
                session_id=resolved_session_id,
            )
            if keys:
                data = INPUT_ENGINE.hotkey(keys)
                maybe_record("desktop_key", {"keys": keys}, data, session_id=resolved_session_id)
            elif key:
                data = INPUT_ENGINE.key_press(key=key, presses=presses, interval_ms=interval_ms)
                maybe_record("desktop_key", {"key": key, "presses": presses, "interval_ms": interval_ms}, data, session_id=resolved_session_id)
            else:
                return err("desktop_key", "Provide either keys (list for combo) or key (string for single key)", started_ms=started, code="validation_error")
            if fg is not None:
                data["foreground"] = {"pre": fg}
            return ok("desktop_key", data=data, started_ms=started)
        except Exception as e:
            return err("desktop_key", str(e), started_ms=started)

    @mcp.tool(
        description=(
            "Advanced mouse operations. "
            "action: move (move cursor to x,y, duration_ms= for smooth), "
            "position (get current cursor position), "
            "down (press and hold button at x,y), "
            "up (release button at x,y), "
            "drag (drag from current position to x,y, duration_ms=)."
        ),
    )
    def desktop_mouse(
        action: str,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        duration_ms: int = 0,
        mouse_down_up: bool = True,
        require_foreground_match: bool = False,
        window_title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        foreground_mismatch_mode: str = "refocus_and_retry",
        foreground_retries: int = 3,
        foreground_retry_wait_ms: int = 150,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        act = action.strip().lower()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)

            if act == "position":
                data = INPUT_ENGINE.position()
                return ok("desktop_mouse", data=data, started_ms=started)

            fg = _guard_foreground(
                require_foreground_match=require_foreground_match,
                window_title_regex=window_title_regex,
                window_handle=window_handle,
                window_pid=window_pid,
                foreground_mismatch_mode=foreground_mismatch_mode,
                foreground_retries=foreground_retries,
                foreground_retry_wait_ms=foreground_retry_wait_ms,
                session_id=resolved_session_id,
            )

            if act == "move":
                if x is None or y is None:
                    return err("desktop_mouse", "x and y are required for move", started_ms=started, code="validation_error")
                data = INPUT_ENGINE.move(x=x, y=y, duration_ms=duration_ms)
            elif act == "down":
                data = INPUT_ENGINE.mouse_down(x=x, y=y, button=button)
            elif act == "up":
                data = INPUT_ENGINE.mouse_up(x=x, y=y, button=button)
            elif act == "drag":
                if x is None or y is None:
                    return err("desktop_mouse", "x and y are required for drag", started_ms=started, code="validation_error")
                data = INPUT_ENGINE.drag_to(x=x, y=y, duration_ms=duration_ms, button=button, mouse_down_up=mouse_down_up)
            else:
                return err("desktop_mouse", f"Unknown action: {action}", hint="Use: move, position, down, up, drag", started_ms=started, code="validation_error")

            if fg is not None:
                data["foreground"] = {"pre": fg}
            maybe_record("desktop_mouse", {"action": act, "x": x, "y": y, "button": button}, data, session_id=resolved_session_id)
            return ok("desktop_mouse", data=data, started_ms=started)
        except Exception as e:
            return err("desktop_mouse", str(e), started_ms=started)

    @mcp.tool(description="Read or write the Windows clipboard. action: read (get text) or write (set text).")
    def desktop_clipboard(
        action: str = "read",
        text: str = "",
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        act = action.strip().lower()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            if act == "read":
                data = INPUT_ENGINE.clipboard_read()
                maybe_record("desktop_clipboard", {"action": "read"}, data, session_id=resolved_session_id)
                return ok("desktop_clipboard", data=data, started_ms=started)
            elif act == "write":
                data = INPUT_ENGINE.clipboard_write(text)
                maybe_record("desktop_clipboard", {"action": "write", "text": text}, data, session_id=resolved_session_id)
                return ok("desktop_clipboard", data=data, started_ms=started)
            else:
                return err("desktop_clipboard", f"Unknown action: {action}", hint="Use: read, write", started_ms=started, code="validation_error")
        except Exception as e:
            return err("desktop_clipboard", str(e), started_ms=started)
