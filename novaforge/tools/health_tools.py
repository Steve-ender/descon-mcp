from __future__ import annotations

import ctypes
from ctypes import wintypes
import platform
import shutil
import sys
from typing import Any

import psutil
from fastmcp import FastMCP

from novaforge.config import load_settings
from novaforge.elevated_broker import ELEVATED_BROKER
from novaforge.failure_envelope import analyze_failure_envelope
from novaforge.engines.activity_indicator import ACTIVITY_INDICATOR
from novaforge.engines.ocr_engine import OCR_ENGINE
from novaforge.engines.screen_engine import SCREEN_ENGINE
from novaforge.engines.window_engine import WINDOW_ENGINE
from novaforge.result import err, now_ms, ok
from novaforge.safety import assert_can_run
from novaforge.state import STATE

TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _process_elevation(pid: int) -> str:
    h_process = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not h_process:
        return "unknown"
    token = wintypes.HANDLE()
    try:
        if not ctypes.windll.advapi32.OpenProcessToken(h_process, TOKEN_QUERY, ctypes.byref(token)):
            return "unknown"
        elevation = wintypes.DWORD()
        size = wintypes.DWORD(ctypes.sizeof(elevation))
        ret_len = wintypes.DWORD()
        okq = ctypes.windll.advapi32.GetTokenInformation(
            token,
            TOKEN_ELEVATION,
            ctypes.byref(elevation),
            size,
            ctypes.byref(ret_len),
        )
        if not okq:
            return "unknown"
        return "elevated" if elevation.value else "standard"
    except Exception:
        return "unknown"
    finally:
        try:
            ctypes.windll.kernel32.CloseHandle(token)
        except Exception:
            pass
        try:
            ctypes.windll.kernel32.CloseHandle(h_process)
        except Exception:
            pass


def register_health_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Server health, diagnostics, and admin operations. "
            "action: capabilities (server version, features, policy defaults), "
            "healthcheck (comprehensive check: session, policy, monitors, OCR, foreground, privilege), "
            "diagnose (privilege/elevation mismatch analysis, window_title_regex=), "
            "failure_envelope (pre-flight plan analysis, plan=, runtime_profile=), "
            "broker_ping (ping elevated broker), "
            "broker_stop (stop elevated broker)."
        ),
    )
    def desktop_health(
        action: str,
        window_title_regex: str | None = None,
        include_windows_sample: bool = True,
        max_window_sample: int = 5,
        plan: list[dict[str, Any]] | None = None,
        runtime_profile: str = "basic_reliable",
        runtime_options: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        act = action.strip().lower()

        if act == "capabilities":
            try:
                settings = load_settings()
                data = {
                    "server": {
                        "name": "NovaForge MCP",
                        "version": "0.1.0",
                        "python": sys.version.split(" ")[0],
                        "platform": platform.platform(),
                    },
                    "features": {
                        "window_binding": True,
                        "foreground_guard": True,
                        "activity_glow": True,
                        "transactions": True,
                        "recording_plan_replay": True,
                        "vision_template_matching": True,
                    },
                    "defaults": {
                        "require_allowlist": settings.require_allowlist,
                        "allowlist_hard_enforce": settings.allowlist_hard_enforce,
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
                        "enable_elevated_broker": settings.enable_elevated_broker,
                        "elevated_broker_command": settings.elevated_broker_command,
                        "elevated_broker_timeout_ms": settings.elevated_broker_timeout_ms,
                        "broker_route_on_privilege_mismatch": settings.broker_route_on_privilege_mismatch,
                    },
                }
                return ok("desktop_health", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_health", str(e), started_ms=started, code="capabilities_failed")

        elif act == "healthcheck":
            try:
                settings = load_settings()
                resolved_session_id = STATE.resolve_session_id(session_id)
                session = STATE.get(session_id=resolved_session_id)
                monitors = SCREEN_ENGINE.monitors()
                foreground = WINDOW_ENGINE.get_foreground_window()
                privilege = WINDOW_ENGINE.privilege_snapshot(pid=foreground.get("pid"))
                windows_sample: list[dict[str, Any]] = []
                if include_windows_sample:
                    windows_sample = WINDOW_ENGINE.list_windows(only_visible=True)[: max(1, int(max_window_sample))]

                has_rapidocr = bool(getattr(OCR_ENGINE, "_rapidocr", None) is not None)
                has_tesseract = bool(shutil.which("tesseract"))
                ffmpeg_available = bool(shutil.which("ffmpeg"))
                policy_ok = True
                policy_notes: list[str] = []
                if settings.require_allowlist and settings.allowlist_hard_enforce and not settings.allowlist:
                    policy_ok = False
                    policy_notes.append("Allowlist is required but empty. Launch/close tools will be blocked.")
                if settings.enable_elevated_broker and not settings.elevated_broker_command:
                    policy_notes.append(
                        "Elevated broker enabled without explicit command. Default module launcher will be used."
                    )
                if settings.enable_elevated_broker and settings.elevated_broker_timeout_ms < 3000:
                    policy_notes.append("Elevated broker timeout is very low and may cause false timeouts.")

                data = {
                    "session": {
                        "session_id": resolved_session_id,
                        "default_session_id": STATE.resolve_session_id(),
                        "session_count": STATE.session_count(),
                        "active": session.active,
                        "action_count": session.action_count,
                        "bound_window_handle": session.bound_window_handle,
                        "bound_window_pid": session.bound_window_pid,
                        "focused_window_title": session.focused_window_title,
                    },
                    "policy": {
                        "require_allowlist": settings.require_allowlist,
                        "allowlist_size": len(settings.allowlist),
                        "allowlist_hard_enforce": settings.allowlist_hard_enforce,
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
                        "policy_ok": policy_ok,
                        "notes": policy_notes,
                    },
                    "capabilities": {
                        "monitors": len(monitors),
                        "foreground_probe": bool(foreground.get("handle")),
                        "rapidocr_available": has_rapidocr,
                        "tesseract_available": has_tesseract,
                        "ffmpeg_available": ffmpeg_available,
                        "activity_glow": ACTIVITY_INDICATOR.status(),
                        "elevated_broker": ELEVATED_BROKER.status(settings=settings),
                    },
                    "foreground": foreground,
                    "privilege": privilege,
                    "windows_sample": windows_sample,
                }
                return ok("desktop_health", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_health", str(e), started_ms=started, code="healthcheck_failed")

        elif act == "diagnose":
            try:
                settings = load_settings()
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)

                self_pid = psutil.Process().pid
                self_name = psutil.Process().name()
                self_admin = _is_admin()
                self_elev = _process_elevation(self_pid)

                target: dict[str, Any] | None = None
                hints: list[str] = []
                if window_title_regex:
                    try:
                        w = WINDOW_ENGINE.resolve_window(title_regex=window_title_regex, timeout_ms=3000)
                        target_pid = int(getattr(w, "process_id", lambda: 0)())
                        target_name = psutil.Process(target_pid).name() if target_pid else ""
                        target_elev = _process_elevation(target_pid) if target_pid else "unknown"
                        target = {
                            "window_title": w.window_text() or "",
                            "pid": target_pid,
                            "process_name": target_name,
                            "elevation": target_elev,
                        }
                        if target_elev == "elevated" and self_elev != "elevated":
                            hints.append("Target window is elevated while automation host is not. Relaunch host with admin rights.")
                        if target_elev == "unknown":
                            hints.append("Could not determine target elevation. Check OS policy/UIPI restrictions.")
                    except Exception as e:
                        target = {"error": str(e)}
                        hints.append("Target window could not be resolved. Verify window_title_regex.")

                hints.append("For secure/UAC prompts, UI automation may be blocked by desktop isolation.")
                hints.append("If actions fail intermittently, ensure target app is foreground and not occluded.")

                data = {
                    "host": {
                        "pid": self_pid,
                        "process_name": self_name,
                        "is_admin": self_admin,
                        "elevation": self_elev,
                    },
                    "target": target,
                    "hints": hints,
                }
                return ok("desktop_health", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_health", str(e), started_ms=started, code="diagnostics_failed")

        elif act == "failure_envelope":
            try:
                settings = load_settings()
                envelope = analyze_failure_envelope(
                    plan=plan or [],
                    settings=settings,
                    runtime_profile=runtime_profile,
                    runtime_options=runtime_options,
                )
                envelope["privilege"] = WINDOW_ENGINE.privilege_snapshot()
                envelope["activity_glow"] = ACTIVITY_INDICATOR.status()
                envelope["elevated_broker"] = ELEVATED_BROKER.status(settings=settings)
                return ok("desktop_health", data=envelope, started_ms=started)
            except Exception as e:
                return err("desktop_health", str(e), started_ms=started, code="failure_envelope_failed")

        elif act == "broker_ping":
            try:
                settings = load_settings()
                data = ELEVATED_BROKER.ping(settings=settings)
                return ok("desktop_health", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_health", str(e), started_ms=started, code="broker_ping_failed")

        elif act == "broker_stop":
            try:
                ELEVATED_BROKER.close()
                return ok("desktop_health", data={"stopped": True}, started_ms=started)
            except Exception as e:
                return err("desktop_health", str(e), started_ms=started, code="broker_stop_failed")

        else:
            return err(
                "desktop_health",
                f"Unknown action: {action}",
                hint="Use one of: capabilities, healthcheck, diagnose, failure_envelope, broker_ping, broker_stop",
                started_ms=started,
                code="validation_error",
            )
