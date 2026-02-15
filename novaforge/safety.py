from __future__ import annotations

from pathlib import Path
import shlex

from .config import Settings
from .state import STATE


def assert_can_run(settings: Settings, session_id: str | None = None) -> str:
    resolved_session_id = STATE.resolve_session_id(session_id)
    st = STATE.get(session_id=resolved_session_id)
    if not st.active:
        STATE.start(session_id=resolved_session_id)
        st = STATE.get(session_id=resolved_session_id)
    if st.emergency_stop:
        raise RuntimeError("Emergency stop is active")
    if st.cancel_requested:
        raise RuntimeError("Session cancel has been requested")
    if st.paused:
        raise RuntimeError("Session is paused")
    if settings.max_actions > 0 and st.action_count >= settings.max_actions:
        raise RuntimeError(f"Max action limit reached: {settings.max_actions}")
    return resolved_session_id


def assert_process_allowed(settings: Settings, process_name: str) -> None:
    normalized = normalize_process_name(process_name)
    if not settings.require_allowlist:
        return
    if normalized not in settings.allowlist and settings.allowlist_hard_enforce:
        raise RuntimeError(f"Process '{process_name}' blocked by allowlist policy")


def normalize_process_name(process_name: str) -> str:
    name = (process_name or "").strip().strip('"').strip("'").lower()
    try:
        base = Path(name).name.lower()
    except Exception:
        base = name
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def process_name_from_command(command: str) -> str:
    if not command.strip():
        return ""
    try:
        parts = shlex.split(command, posix=False)
    except Exception:
        parts = command.split(" ")
    if not parts:
        return ""
    return normalize_process_name(parts[0])
