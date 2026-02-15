from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class SessionState:
    active: bool = False
    started_at: str | None = None
    action_count: int = 0
    emergency_stop: bool = False
    paused: bool = False
    cancel_requested: bool = False
    focused_window_title: str | None = None
    focused_window_handle: int | None = None
    focused_window_pid: int | None = None
    bound_window_title_regex: str | None = None
    bound_window_handle: int | None = None
    bound_window_pid: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    recording_active: bool = False
    recording_started_at: str | None = None
    recording_name: str | None = None
    recording_events: list[dict[str, Any]] = field(default_factory=list)
    transaction_active: bool = False
    transaction_id: str | None = None
    transaction_name: str | None = None
    transaction_started_at: str | None = None
    transaction_committed: bool | None = None
    transaction_summary: str | None = None
    transaction_checkpoints: list[dict[str, Any]] = field(default_factory=list)
    transaction_hints: list[dict[str, Any]] = field(default_factory=list)


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionState] = {"default": SessionState()}
        self._default_session_id = "default"
        self._local = threading.local()

    def _normalize_session_id(self, session_id: str | None) -> str:
        raw = str(session_id or "").strip()
        return raw or "default"

    def resolve_session_id(self, session_id: str | None = None) -> str:
        if session_id is not None:
            return self._normalize_session_id(session_id)
        local_session = getattr(self._local, "session_id", None)
        if isinstance(local_session, str) and local_session.strip():
            return self._normalize_session_id(local_session)
        return self._default_session_id

    def set_default_session(self, session_id: str) -> str:
        with self._lock:
            sid = self._normalize_session_id(session_id)
            self._default_session_id = sid
            self._sessions.setdefault(sid, SessionState())
            return sid

    def use_session(self, session_id: str | None = None) -> str:
        sid = self.resolve_session_id(session_id)
        self._local.session_id = sid
        return sid

    def clear_thread_session(self) -> None:
        if hasattr(self._local, "session_id"):
            self._local.session_id = None

    def list_session_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._sessions.keys())

    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _get_or_create_locked(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState()
        return self._sessions[session_id]

    def _active_session_count_locked(self) -> int:
        return sum(1 for item in self._sessions.values() if item.active)

    def get(self, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            return copy.deepcopy(self._get_or_create_locked(sid))

    def start(self, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            was_any_active = self._active_session_count_locked() > 0
            self._sessions[sid] = SessionState(
                active=True,
                started_at=datetime.now(timezone.utc).isoformat(),
                action_count=0,
                emergency_stop=False,
            )
            self._default_session_id = sid
            self._local.session_id = sid
            if not was_any_active:
                try:
                    from descon.engines.activity_indicator import ACTIVITY_INDICATOR

                    ACTIVITY_INDICATOR.start()
                except Exception:
                    pass
            return copy.deepcopy(self._sessions[sid])

    def stop(self, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.active = False
            if self._active_session_count_locked() == 0:
                try:
                    from descon.engines.activity_indicator import ACTIVITY_INDICATOR

                    ACTIVITY_INDICATOR.stop()
                except Exception:
                    pass
            return copy.deepcopy(current)

    def mark_action(self, session_id: str | None = None) -> int:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.action_count += 1
            try:
                from descon.engines.activity_indicator import ACTIVITY_INDICATOR

                ACTIVITY_INDICATOR.pulse()
            except Exception:
                pass
            return current.action_count

    def set_focus(
        self,
        title: str | None,
        handle: int | None = None,
        pid: int | None = None,
        session_id: str | None = None,
    ) -> None:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.focused_window_title = title
            current.focused_window_handle = handle
            current.focused_window_pid = pid

    def bind_window(
        self,
        title_regex: str | None = None,
        handle: int | None = None,
        pid: int | None = None,
        session_id: str | None = None,
    ) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.bound_window_title_regex = title_regex
            current.bound_window_handle = handle
            current.bound_window_pid = pid
            return copy.deepcopy(current)

    def clear_window_binding(self, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.bound_window_title_regex = None
            current.bound_window_handle = None
            current.bound_window_pid = None
            return copy.deepcopy(current)

    def set_emergency_stop(self, value: bool, session_id: str | None = None) -> None:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.emergency_stop = value

    def set_paused(self, value: bool, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.paused = bool(value)
            return copy.deepcopy(current)

    def set_cancel_requested(self, value: bool, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.cancel_requested = bool(value)
            return copy.deepcopy(current)

    def recording_start(self, name: str | None = None, clear: bool = False, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.recording_active = True
            current.recording_started_at = datetime.now(timezone.utc).isoformat()
            current.recording_name = name
            if clear:
                current.recording_events = []
            return copy.deepcopy(current)

    def recording_stop(self, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.recording_active = False
            return copy.deepcopy(current)

    def recording_clear(self, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.recording_events = []
            return copy.deepcopy(current)

    def recording_append(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> None:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            if not current.recording_active:
                return
            current.recording_events.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "action": action,
                    "params": params or {},
                    "data": data or {},
                }
            )

    def recording_get_events(self, session_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            return copy.deepcopy(current.recording_events)

    def recording_set_events(self, events: list[dict[str, Any]], merge: bool = False, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            if not merge:
                current.recording_events = []
            for item in events:
                if isinstance(item, dict):
                    current.recording_events.append(copy.deepcopy(item))
            return copy.deepcopy(current)

    def transaction_start(self, tx_id: str, name: str | None = None, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.transaction_active = True
            current.transaction_id = tx_id
            current.transaction_name = name
            current.transaction_started_at = datetime.now(timezone.utc).isoformat()
            current.transaction_committed = None
            current.transaction_summary = None
            current.transaction_checkpoints = []
            current.transaction_hints = []
            return copy.deepcopy(current)

    def transaction_add_checkpoint(self, checkpoint: dict[str, Any], session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.transaction_checkpoints.append(copy.deepcopy(checkpoint))
            return copy.deepcopy(current)

    def transaction_add_hint(self, hint: dict[str, Any], session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.transaction_hints.append(copy.deepcopy(hint))
            return copy.deepcopy(current)

    def transaction_end(self, committed: bool, summary: str | None = None, session_id: str | None = None) -> SessionState:
        with self._lock:
            sid = self.resolve_session_id(session_id)
            current = self._get_or_create_locked(sid)
            current.transaction_active = False
            current.transaction_committed = bool(committed)
            current.transaction_summary = summary
            return copy.deepcopy(current)


STATE = StateStore()
