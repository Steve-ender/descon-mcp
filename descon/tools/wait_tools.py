from __future__ import annotations

import re
import time

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


def register_wait_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Wait/sleep or wait for a condition. "
            "Simple sleep: desktop_wait(time_ms=2000). "
            "Smart wait — set 'until' to: "
            "screen_stable (wait until screen pixels stop changing), "
            "text_visible (wait until text appears on screen, requires text=), "
            "text_gone (wait until text disappears from screen, requires text=), "
            "window_exists (wait until a window appears, requires title_regex=). "
            "All smart waits use timeout_ms (default 10000) and poll_ms (default 500)."
        ),
    )
    def desktop_wait(
        time_ms: int = 0,
        until: str | None = None,
        text: str | None = None,
        title_regex: str | None = None,
        timeout_ms: int = 10000,
        poll_ms: int = 500,
        monitor_index: int = 1,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)

            if until is None:
                # Simple sleep
                data = INPUT_ENGINE.sleep(time_ms)
                maybe_record("desktop_wait", {"time_ms": time_ms}, data, session_id=resolved_session_id)
                return ok("desktop_wait", data=data, started_ms=started)

            mode = until.strip().lower()
            deadline = time.time() + max(0, timeout_ms) / 1000.0
            attempts = 0

            if mode == "screen_stable":
                prev_b64 = None
                while True:
                    attempts += 1
                    shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index)
                    cur_b64 = shot.get("base64_png", "")
                    if prev_b64 is not None and cur_b64 and prev_b64 == cur_b64:
                        data = {"until": mode, "stable": True, "attempts": attempts}
                        maybe_record("desktop_wait", {"until": mode, "timeout_ms": timeout_ms}, data, session_id=resolved_session_id)
                        return ok("desktop_wait", data=data, started_ms=started)
                    prev_b64 = cur_b64
                    if time.time() >= deadline:
                        return err("desktop_wait", f"Screen did not stabilize within {timeout_ms}ms", started_ms=started, code="timeout")
                    INPUT_ENGINE.sleep(max(50, poll_ms))

            elif mode in {"text_visible", "text_gone"}:
                if not (text or "").strip():
                    return err("desktop_wait", f"{mode} requires text= parameter", started_ms=started, code="validation_error")
                want_visible = mode == "text_visible"
                while True:
                    attempts += 1
                    shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index)
                    try:
                        ranked = OCR_ENGINE.select_text_target_local_model(
                            image_path=str(shot["path"]),
                            query=text,
                        )
                        found = ranked.get("count", 0) > 0
                    except Exception:
                        found = False
                    if found == want_visible:
                        data = {"until": mode, "found": found, "text": text, "attempts": attempts}
                        maybe_record("desktop_wait", {"until": mode, "text": text, "timeout_ms": timeout_ms}, data, session_id=resolved_session_id)
                        return ok("desktop_wait", data=data, started_ms=started)
                    if time.time() >= deadline:
                        return err(
                            "desktop_wait",
                            f"Text {'not found' if want_visible else 'still visible'}: {text} (after {timeout_ms}ms)",
                            started_ms=started,
                            code="timeout",
                        )
                    INPUT_ENGINE.sleep(max(50, poll_ms))

            elif mode == "window_exists":
                if not (title_regex or "").strip():
                    return err("desktop_wait", "window_exists requires title_regex= parameter", started_ms=started, code="validation_error")
                try:
                    re.compile(title_regex)
                except re.error as e:
                    return err("desktop_wait", f"Invalid regex: {e}", started_ms=started, code="validation_error")
                while True:
                    attempts += 1
                    windows = WINDOW_ENGINE.list_windows(only_visible=True)
                    matched = any(re.search(title_regex, w.get("title", ""), re.IGNORECASE) for w in windows)
                    if matched:
                        data = {"until": mode, "found": True, "title_regex": title_regex, "attempts": attempts}
                        maybe_record("desktop_wait", {"until": mode, "title_regex": title_regex, "timeout_ms": timeout_ms}, data, session_id=resolved_session_id)
                        return ok("desktop_wait", data=data, started_ms=started)
                    if time.time() >= deadline:
                        return err(
                            "desktop_wait",
                            f"Window matching '{title_regex}' not found within {timeout_ms}ms",
                            started_ms=started,
                            code="timeout",
                        )
                    INPUT_ENGINE.sleep(max(50, poll_ms))

            else:
                return err(
                    "desktop_wait",
                    f"Unknown until mode: {until}",
                    hint="Use: screen_stable, text_visible, text_gone, window_exists",
                    started_ms=started,
                    code="validation_error",
                )
        except Exception as e:
            return err("desktop_wait", str(e), started_ms=started)
