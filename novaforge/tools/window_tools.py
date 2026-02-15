from __future__ import annotations

from fastmcp import FastMCP

from novaforge.config import load_settings
from novaforge.engines.window_engine import WINDOW_ENGINE
from novaforge.result import err, now_ms, ok
from novaforge.safety import assert_can_run, assert_process_allowed, process_name_from_command
from novaforge.state import STATE
from novaforge.tools.recording_tools import maybe_record


def register_window_tools(mcp: FastMCP) -> None:

    def _effective_target(
        title_regex: str | None,
        window_handle: int | None,
        window_pid: int | None,
        session_id: str | None = None,
    ) -> tuple[str | None, int | None, int | None]:
        if window_handle is not None or window_pid is not None or (title_regex or "").strip():
            return title_regex, window_handle, window_pid
        st = STATE.get(session_id=session_id)
        return st.bound_window_title_regex, st.bound_window_handle, st.bound_window_pid

    def _err_code(e: Exception) -> str:
        msg = str(e).lower()
        if "foreground mismatch" in msg:
            return "foreground_mismatch"
        if "foreground refocus failed" in msg:
            return "foreground_refocus_failed"
        if "foreground guard requires a window target" in msg:
            return "foreground_unresolvable_target"
        if "privilege mismatch" in msg or "administrator" in msg:
            return "permission_denied"
        if "allowlist" in msg:
            return "policy_blocked"
        if "not found" in msg or "no running process matched" in msg:
            return "not_found"
        if "timed out" in msg:
            return "timeout"
        return "runtime_error"

    @mcp.tool(
        description=(
            "Window management. "
            "action: list (list open windows, only_visible=), "
            "focus (bring window to foreground by title_regex, window_handle, or window_pid), "
            "bind (set default target window for session, title_regex/window_handle/window_pid, focus=), "
            "unbind (clear bound window), "
            "binding_status (check bound/focused window), "
            "foreground (get current foreground window info), "
            "focus_guard (verify expected window is foreground, mismatch_mode=), "
            "control (control_action=minimize/maximize/restore/close/move/resize/move_resize, x=,y=,width=,height=), "
            "launch (launch app by command, shell_mode=, cwd=), "
            "close (close app by pid or name), "
            "element_tree (dump UI Automation tree, max_depth=, max_children=), "
            "find_element (find UI elements by title/auto_id/control_type, max_results=)."
        ),
    )
    def desktop_window(
        action: str,
        title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        only_visible: bool = True,
        focus: bool = True,
        timeout_ms: int = 5000,
        mismatch_mode: str = "refocus_and_retry",
        retries: int = 3,
        retry_wait_ms: int = 150,
        resolve_timeout_ms: int = 4000,
        control_action: str | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        command: str | None = None,
        process_name_for_policy: str | None = None,
        shell_mode: bool = False,
        cwd: str | None = None,
        pid: int | None = None,
        name: str | None = None,
        max_depth: int = 3,
        max_children: int = 25,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        found_index: int = 0,
        max_results: int = 10,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        act = action.strip().lower()

        if act == "list":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                data = WINDOW_ENGINE.list_windows(only_visible=only_visible)
                return ok("desktop_window", data={"windows": data, "count": len(data)}, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "focus":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                if window_handle is not None:
                    data = WINDOW_ENGINE.focus_window_by_handle(window_handle=window_handle, timeout_ms=timeout_ms)
                elif window_pid is not None:
                    data = WINDOW_ENGINE.focus_window_by_pid(pid=window_pid, timeout_ms=timeout_ms)
                elif title_regex:
                    data = WINDOW_ENGINE.focus_window(title_regex=title_regex, timeout_ms=timeout_ms)
                else:
                    return err(
                        "desktop_window",
                        "Provide title_regex, window_handle, or window_pid",
                        started_ms=started,
                        code="validation_error",
                    )
                STATE.set_focus(data.get("title"), handle=data.get("handle"), pid=data.get("pid"), session_id=resolved_session_id)
                maybe_record(
                    "desktop_window",
                    {"action": "focus", "title_regex": title_regex, "window_handle": window_handle, "window_pid": window_pid, "timeout_ms": timeout_ms},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "bind":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                if window_handle is None and window_pid is None and not (title_regex or "").strip():
                    return err(
                        "desktop_window",
                        "Provide one of window_handle, window_pid, or title_regex",
                        started_ms=started,
                        code="validation_error",
                    )
                STATE.mark_action(session_id=resolved_session_id)
                window = WINDOW_ENGINE.resolve_window(
                    title_regex=title_regex,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    timeout_ms=timeout_ms,
                )
                wpid: int | None = None
                try:
                    wpid = int(window.process_id())
                except Exception:
                    pass
                data = {
                    "title": window.window_text() or "",
                    "handle": int(window.handle),
                    "pid": wpid,
                    "bound_title_regex": title_regex,
                }
                if focus:
                    focused = WINDOW_ENGINE.focus_window_by_handle(window_handle=data["handle"], timeout_ms=min(timeout_ms, 3000))
                    data["focused"] = focused
                STATE.bind_window(
                    title_regex=title_regex,
                    handle=data["handle"],
                    pid=data.get("pid"),
                    session_id=resolved_session_id,
                )
                STATE.set_focus(
                    data["title"],
                    handle=data["handle"],
                    pid=data["pid"],
                    session_id=resolved_session_id,
                )
                maybe_record(
                    "desktop_window",
                    {"action": "bind", "title_regex": title_regex, "window_handle": window_handle, "window_pid": window_pid, "focus": focus, "timeout_ms": timeout_ms},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "unbind":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                st = STATE.clear_window_binding(session_id=resolved_session_id)
                data = {
                    "bound_window_title_regex": st.bound_window_title_regex,
                    "bound_window_handle": st.bound_window_handle,
                    "bound_window_pid": st.bound_window_pid,
                }
                maybe_record("desktop_window", {"action": "unbind"}, data, session_id=resolved_session_id)
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "binding_status":
            resolved_session_id = STATE.resolve_session_id(session_id)
            st = STATE.get(session_id=resolved_session_id)
            return ok(
                "desktop_window",
                data={
                    "bound_window_title_regex": st.bound_window_title_regex,
                    "bound_window_handle": st.bound_window_handle,
                    "bound_window_pid": st.bound_window_pid,
                    "focused_window_title": st.focused_window_title,
                    "focused_window_handle": st.focused_window_handle,
                    "focused_window_pid": st.focused_window_pid,
                },
                started_ms=started,
            )

        elif act == "foreground":
            try:
                assert_can_run(settings, session_id=session_id)
                data = WINDOW_ENGINE.get_foreground_window()
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "focus_guard":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                target_regex, target_handle, target_pid = _effective_target(
                    title_regex=title_regex,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    session_id=resolved_session_id,
                )
                data = WINDOW_ENGINE.ensure_foreground_target(
                    expected_handle=target_handle,
                    expected_pid=target_pid,
                    expected_title_regex=target_regex,
                    mismatch_mode=mismatch_mode,
                    retries=retries,
                    retry_wait_ms=retry_wait_ms,
                    resolve_timeout_ms=resolve_timeout_ms,
                )
                maybe_record(
                    "desktop_window",
                    {
                        "action": "focus_guard",
                        "title_regex": target_regex,
                        "window_handle": target_handle,
                        "window_pid": target_pid,
                        "mismatch_mode": mismatch_mode,
                        "retries": retries,
                        "retry_wait_ms": retry_wait_ms,
                        "resolve_timeout_ms": resolve_timeout_ms,
                    },
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "control":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                ca = (control_action or "").strip().lower()
                if not ca:
                    return err(
                        "desktop_window",
                        "control_action is required for action=control",
                        hint="Use one of: minimize, maximize, restore, close, move, resize, move_resize",
                        started_ms=started,
                        code="validation_error",
                    )
                data = WINDOW_ENGINE.window_control(
                    action=ca,
                    title_regex=title_regex,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    timeout_ms=timeout_ms,
                )
                maybe_record(
                    "desktop_window",
                    {"action": "control", "control_action": ca, "title_regex": title_regex, "x": x, "y": y, "width": width, "height": height},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "launch":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                if not command:
                    return err("desktop_window", "command is required for action=launch", started_ms=started, code="validation_error")
                pname = process_name_for_policy or process_name_from_command(command)
                assert_process_allowed(settings, pname)
                STATE.mark_action(session_id=resolved_session_id)
                data = WINDOW_ENGINE.launch_app(command=command, shell_mode=shell_mode, cwd=cwd, env=None)
                maybe_record(
                    "desktop_window",
                    {"action": "launch", "command": command, "shell_mode": shell_mode, "cwd": cwd},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "close":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                if name:
                    assert_process_allowed(settings, name)
                STATE.mark_action(session_id=resolved_session_id)
                data = WINDOW_ENGINE.close_app(pid=pid, name=name)
                maybe_record("desktop_window", {"action": "close", "pid": pid, "name": name}, data, session_id=resolved_session_id)
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "element_tree":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                data = WINDOW_ENGINE.element_tree(
                    title_regex=title_regex,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    max_depth=max_depth,
                    max_children=max_children,
                    timeout_ms=timeout_ms,
                )
                return ok("desktop_window", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        elif act == "find_element":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                data = WINDOW_ENGINE.find_elements(
                    window_title_regex=title_regex,
                    title=title,
                    auto_id=auto_id,
                    control_type=control_type,
                    found_index=found_index,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    max_results=max_results,
                    timeout_ms=timeout_ms,
                )
                maybe_record(
                    "desktop_window",
                    {"action": "find_element", "title_regex": title_regex, "title": title, "auto_id": auto_id, "control_type": control_type},
                    {"count": len(data)},
                    session_id=resolved_session_id,
                )
                return ok("desktop_window", data={"matches": data, "count": len(data)}, started_ms=started)
            except Exception as e:
                return err("desktop_window", str(e), started_ms=started, code=_err_code(e))

        else:
            return err(
                "desktop_window",
                f"Unknown action: {action}",
                hint="Use one of: list, focus, bind, unbind, binding_status, foreground, focus_guard, control, launch, close, element_tree, find_element",
                started_ms=started,
                code="validation_error",
            )
