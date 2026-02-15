from __future__ import annotations

from fastmcp import FastMCP

from descon.config import load_settings
from descon.engines.activity_indicator import ACTIVITY_INDICATOR
from descon.result import err, now_ms, ok
from descon.state import STATE


def register_session_tools(mcp: FastMCP) -> None:

    def _with_session(data: dict, session_id: str) -> dict:
        out = dict(data)
        out["session_id"] = session_id
        out["default_session_id"] = STATE.resolve_session_id()
        return out

    @mcp.tool(
        description=(
            "Manage desktop automation sessions. "
            "action: start (begin session, mode=restart|reuse|fail), "
            "stop (end session), status (get session info), "
            "pause (reject actions until resumed), resume (unpause), "
            "cancel (cancel running desktop_act plan), "
            "emergency_stop (halt all automation, enabled=true|false), "
            "policy (show safety settings), "
            "glow_status (check activity indicator)."
        ),
    )
    def desktop_session(
        action: str,
        mode: str = "restart",
        enabled: bool = True,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        act = action.strip().lower()

        if act == "start":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                current = STATE.get(session_id=resolved_session_id)
                m = (mode or "restart").strip().lower()
                if m not in {"restart", "reuse", "fail"}:
                    return err(
                        "desktop_session",
                        f"Unsupported mode: {mode}",
                        hint="Use one of: restart, reuse, fail",
                        started_ms=started,
                        code="validation_error",
                    )
                if current.active:
                    if m == "fail":
                        return err(
                            "desktop_session",
                            "Session already active",
                            hint="Use mode=reuse or mode=restart",
                            started_ms=started,
                            code="session_already_active",
                        )
                    if m == "reuse":
                        STATE.set_default_session(resolved_session_id)
                        STATE.use_session(resolved_session_id)
                        return ok(
                            "desktop_session",
                            data=_with_session(current.__dict__, resolved_session_id),
                            started_ms=started,
                        )
                st = STATE.start(session_id=resolved_session_id)
                return ok("desktop_session", data=_with_session(st.__dict__, resolved_session_id), started_ms=started)
            except Exception as e:
                return err("desktop_session", str(e), started_ms=started)

        elif act == "stop":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                st = STATE.stop(session_id=resolved_session_id)
                return ok("desktop_session", data=_with_session(st.__dict__, resolved_session_id), started_ms=started)
            except Exception as e:
                return err("desktop_session", str(e), started_ms=started)

        elif act == "status":
            resolved_session_id = STATE.resolve_session_id(session_id)
            st = STATE.get(session_id=resolved_session_id)
            data = _with_session(st.__dict__, resolved_session_id)
            data["session_count"] = STATE.session_count()
            data["known_sessions"] = STATE.list_session_ids()
            return ok("desktop_session", data=data, started_ms=started)

        elif act == "pause":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                st = STATE.set_paused(True, session_id=resolved_session_id)
                return ok("desktop_session", data=_with_session(st.__dict__, resolved_session_id), started_ms=started)
            except Exception as e:
                return err("desktop_session", str(e), started_ms=started)

        elif act == "resume":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                st = STATE.set_paused(False, session_id=resolved_session_id)
                st = STATE.set_cancel_requested(False, session_id=resolved_session_id)
                return ok("desktop_session", data=_with_session(st.__dict__, resolved_session_id), started_ms=started)
            except Exception as e:
                return err("desktop_session", str(e), started_ms=started)

        elif act == "cancel":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                st = STATE.set_cancel_requested(True, session_id=resolved_session_id)
                return ok("desktop_session", data=_with_session(st.__dict__, resolved_session_id), started_ms=started)
            except Exception as e:
                return err("desktop_session", str(e), started_ms=started)

        elif act == "emergency_stop":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                STATE.set_emergency_stop(enabled, session_id=resolved_session_id)
                return ok(
                    "desktop_session",
                    data={"enabled": enabled, "session_id": resolved_session_id, "default_session_id": STATE.resolve_session_id()},
                    started_ms=started,
                )
            except Exception as e:
                return err("desktop_session", str(e), started_ms=started)

        elif act == "policy":
            settings = load_settings()
            return ok(
                "desktop_session",
                data={
                    "require_allowlist": settings.require_allowlist,
                    "allowlist_hard_enforce": settings.allowlist_hard_enforce,
                    "allowlist": sorted(settings.allowlist),
                    "max_actions": settings.max_actions,
                    "auto_start_session": settings.auto_start_session,
                    "input_failsafe": settings.input_failsafe,
                    "enable_host_ocr": settings.enable_host_ocr,
                    "window_resolve_fallback_foreground": settings.window_resolve_fallback_foreground,
                    "strict_window_visibility": settings.strict_window_visibility,
                    "strict_privilege_check": settings.strict_privilege_check,
                    "allow_unknown_actions": settings.allow_unknown_actions,
                    "best_effort_vision": settings.best_effort_vision,
                    "best_effort_ocr": settings.best_effort_ocr,
                    "artifacts_dir": settings.artifacts_dir,
                    "enable_activity_glow": settings.enable_activity_glow,
                    "activity_glow_color": settings.activity_glow_color,
                    "activity_glow_thickness": settings.activity_glow_thickness,
                },
                started_ms=started,
            )

        elif act == "glow_status":
            try:
                return ok("desktop_session", data=ACTIVITY_INDICATOR.status(), started_ms=started)
            except Exception as e:
                return err("desktop_session", str(e), started_ms=started)

        else:
            return err(
                "desktop_session",
                f"Unknown action: {action}",
                hint="Use one of: start, stop, status, pause, resume, cancel, emergency_stop, policy, glow_status",
                started_ms=started,
                code="validation_error",
            )
