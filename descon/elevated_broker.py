from __future__ import annotations

import atexit
import json
import shlex
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from queue import Empty, Queue
from typing import Any

from descon.config import Settings
from descon.engines.window_engine import WINDOW_ENGINE


BROKER_ROUTABLE_ACTIONS = {
    "move_mouse",
    "click",
    "mouse_down",
    "mouse_up",
    "drag_to",
    "type",
    "hotkey",
    "key_press",
    "click_template",
    "click_element",
    "element_click",
    "type_element",
    "element_type",
    "element_action",
    "launch_app",
    "close_app",
}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


class ElevatedBrokerClient:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._queue: Queue[dict[str, Any]] = Queue()
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._pending: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._pending_limit = 1000
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._last_failure_ms: int | None = None
        self._cooldown_until_ms: int = 0
        atexit.register(self.close)

    def _reader(self, pipe) -> None:
        while True:
            line = pipe.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                self._queue.put(json.loads(line))
            except Exception:
                continue

    def _command(self, settings: Settings) -> list[str]:
        raw = settings.elevated_broker_command.strip()
        if raw:
            return shlex.split(raw, posix=False)
        return [sys.executable, "-m", "descon.elevated_broker_server"]

    def _start_if_needed(self, settings: Settings) -> None:
        with self._lock:
            now_ms = int(time.time() * 1000)
            if now_ms < self._cooldown_until_ms:
                wait_ms = self._cooldown_until_ms - now_ms
                raise RuntimeError(f"Elevated broker in cooldown after repeated failures ({wait_ms}ms remaining)")
            if self._proc is not None and self._proc.poll() is None:
                return
            cmd = self._command(settings)
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            if self._proc.stdout is None:
                raise RuntimeError("Failed to start elevated broker: stdout unavailable")
            self._reader_thread = threading.Thread(target=self._reader, args=(self._proc.stdout,), daemon=True)
            self._reader_thread.start()

    def _record_failure(self, error: str, *, now_ms: int | None = None) -> None:
        ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
        # Do not keep extending cooldown on repeated cooldown denials.
        if "in cooldown" in error.lower() and ts < self._cooldown_until_ms:
            self._last_error = error
            self._last_failure_ms = ts
            return
        self._consecutive_failures += 1
        self._last_error = error
        self._last_failure_ms = ts
        if self._consecutive_failures >= 3:
            self._cooldown_until_ms = ts + 3000

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._last_error = None
        self._last_failure_ms = None
        self._cooldown_until_ms = 0

    def _take_response(self, req_id: str, timeout_s: float) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.pop(req_id, None)
            if pending is not None:
                return pending
        deadline = time.monotonic() + max(0.01, float(timeout_s))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc = self._proc
                if proc is not None:
                    code = proc.poll()
                    if code is not None:
                        raise RuntimeError(f"Elevated broker exited unexpectedly (code={code})")
                raise RuntimeError("Timed out waiting for elevated broker response")
            try:
                msg = self._queue.get(timeout=remaining)
            except Empty as e:
                proc = self._proc
                if proc is not None:
                    code = proc.poll()
                    if code is not None:
                        raise RuntimeError(f"Elevated broker exited unexpectedly (code={code})") from e
                raise RuntimeError("Timed out waiting for elevated broker response") from e
            msg_id = str(msg.get("id") or "")
            if msg_id == req_id:
                return msg
            if msg_id:
                with self._lock:
                    if len(self._pending) >= self._pending_limit:
                        # Bound memory growth while preserving most recent unmatched responses.
                        self._pending.popitem(last=False)
                    self._pending[msg_id] = msg

    def _request(self, settings: Settings, op: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._request_lock:
            last_error: Exception | None = None
            for attempt in (1, 2):
                try:
                    timeout_s = max(1.0, settings.elevated_broker_timeout_ms / 1000.0)
                    with self._lock:
                        self._start_if_needed(settings)
                        if self._proc is None or self._proc.stdin is None:
                            raise RuntimeError("Elevated broker process is not available")
                        req_id = str(uuid.uuid4())
                        req = {"id": req_id, "op": op, "payload": payload or {}}
                        try:
                            self._proc.stdin.write(json.dumps(req, separators=(",", ":")) + "\n")
                            self._proc.stdin.flush()
                        except Exception as e:
                            raise RuntimeError(f"Failed to send request to elevated broker: {e}") from e
                    msg = self._take_response(req_id=req_id, timeout_s=timeout_s)
                    with self._lock:
                        self._record_success()
                    return msg
                except Exception as e:
                    last_error = e
                    with self._lock:
                        self._record_failure(str(e))
                    recoverable = any(
                        x in str(e).lower()
                        for x in [
                            "failed to send request",
                            "process is not available",
                            "exited unexpectedly",
                            "timed out waiting for elevated broker response",
                        ]
                    )
                    if attempt == 1 and recoverable:
                        self._shutdown_process(preserve_failure_state=True)
                        continue
                    break
            raise RuntimeError(str(last_error or "Broker request failed"))

    def status(self, settings: Settings) -> dict[str, Any]:
        explicit_cmd = bool(settings.elevated_broker_command.strip())
        configuration_source = "explicit" if explicit_cmd else "default"
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            pending_count = len(self._pending)
            consecutive_failures = self._consecutive_failures
            last_error = self._last_error
            last_failure_ms = self._last_failure_ms
            cooldown_until_ms = self._cooldown_until_ms
            proc = self._proc
        if not running:
            exit_code = None
            if proc is not None:
                exit_code = proc.poll()
            return {
                "running": False,
                "exit_code": exit_code,
                "configured": True,
                "configuration_source": configuration_source,
                "enabled": bool(settings.enable_elevated_broker),
                "pending_count": pending_count,
                "consecutive_failures": consecutive_failures,
                "last_error": last_error,
                "last_failure_ms": last_failure_ms,
                "cooldown_until_ms": cooldown_until_ms,
            }
        return {
            "running": True,
            "pid": proc.pid if proc is not None else None,
            "configured": True,
            "configuration_source": configuration_source,
            "enabled": bool(settings.enable_elevated_broker),
            "pending_count": pending_count,
            "consecutive_failures": consecutive_failures,
            "last_error": last_error,
            "last_failure_ms": last_failure_ms,
            "cooldown_until_ms": cooldown_until_ms,
        }

    def ping(self, settings: Settings) -> dict[str, Any]:
        if not settings.enable_elevated_broker:
            raise RuntimeError("Elevated broker is disabled by policy")
        msg = self._request(settings, "ping", {})
        if not bool(msg.get("ok")):
            raise RuntimeError(str((msg.get("error") or {}).get("message", "Broker ping failed")))
        data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
        return data

    def execute_step(
        self,
        *,
        settings: Settings,
        step: dict[str, Any],
        runtime_profile: str,
        runtime_options: dict[str, Any] | None,
        default_ocr_backend: str,
    ) -> dict[str, Any]:
        if not settings.enable_elevated_broker:
            raise RuntimeError("Elevated broker is disabled by policy")
        payload = {
            "step": step,
            "runtime_profile": runtime_profile,
            "runtime_options": runtime_options or {},
            "default_ocr_backend": default_ocr_backend,
        }
        msg = self._request(settings, "execute_step", payload)
        if not bool(msg.get("ok")):
            err = msg.get("error") if isinstance(msg.get("error"), dict) else {}
            raise RuntimeError(str(err.get("message") or "Broker step execution failed"))
        data = msg.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Broker returned invalid payload")
        return data

    def should_route_step(
        self,
        *,
        settings: Settings,
        step: dict[str, Any],
        action: str,
        runtime_options: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        opts = runtime_options or {}
        if _coerce_bool(opts.get("broker_disabled")):
            return False, "broker_disabled_option"
        broker_mode = str(opts.get("broker_mode", "auto")).strip().lower()
        if broker_mode not in {"auto", "never", "always"}:
            broker_mode = "auto"
        force_local = _coerce_bool(step.get("force_local"))
        force_broker = _coerce_bool(step.get("force_broker"))
        if force_local and force_broker:
            return False, "conflicting_route_directives"
        if force_local:
            return False, "force_local"
        if force_broker:
            return True, "force_broker"
        if broker_mode == "never":
            return False, "broker_mode_never"
        if broker_mode == "always":
            if not settings.enable_elevated_broker:
                return True, "broker_mode_always_disabled"
            return True, "broker_mode_always"
        if action not in BROKER_ROUTABLE_ACTIONS:
            return False, "not_routable_action"
        if not settings.enable_elevated_broker:
            return False, "broker_disabled_policy"
        if not settings.broker_route_on_privilege_mismatch:
            return False, "policy_no_auto_route"
        target_pid = step.get("window_pid")
        snap = WINDOW_ENGINE.privilege_snapshot(pid=int(target_pid) if isinstance(target_pid, int) else None)
        if bool(snap.get("privilege_mismatch_risk")):
            return True, "privilege_mismatch_risk"
        return False, "no_mismatch"

    def _shutdown_process(self, preserve_failure_state: bool) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._pending = OrderedDict()
            if not preserve_failure_state:
                self._consecutive_failures = 0
                self._last_error = None
                self._last_failure_ms = None
                self._cooldown_until_ms = 0
            while True:
                try:
                    self._queue.get_nowait()
                except Exception:
                    break
            if proc is None:
                return
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.5)
                    except Exception:
                        proc.kill()
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                if proc.stdout is not None:
                    try:
                        proc.stdout.close()
                    except Exception:
                        pass
            except Exception:
                pass

    def close(self) -> None:
        self._shutdown_process(preserve_failure_state=False)


ELEVATED_BROKER = ElevatedBrokerClient()
