from __future__ import annotations

import atexit
import json
import subprocess
import sys
import threading
from typing import Any

from novaforge.config import load_settings


class ActivityIndicator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[str] | None = None
        self._enabled = False
        self._last_error: str | None = None
        self.configure()
        atexit.register(self.stop)

    def configure(self) -> None:
        s = load_settings()
        self._enabled = bool(s.enable_activity_glow)

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = bool(self._proc is not None and self._proc.poll() is None)
            return {
                "enabled": self._enabled,
                "running": running,
                "last_error": self._last_error,
            }

    def _is_running(self) -> bool:
        return bool(self._proc is not None and self._proc.poll() is None)

    def _send(self, payload: dict[str, Any]) -> None:
        with self._lock:
            if not self._is_running():
                return
            proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except Exception as e:
            self._last_error = str(e)

    def start(self) -> None:
        self.configure()
        if not self._enabled:
            return
        with self._lock:
            if self._is_running():
                self._send({"op": "active", "value": True})
                return
            settings = load_settings()
            cmd = [
                sys.executable,
                "-m",
                "novaforge.engines.activity_overlay_worker",
                "--color",
                settings.activity_glow_color,
                "--thickness",
                str(max(2, int(settings.activity_glow_thickness))),
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except Exception as e:
                self._proc = None
                self._last_error = str(e)
                return
        self._send({"op": "active", "value": True})

    def stop(self) -> None:
        with self._lock:
            if not self._is_running():
                self._proc = None
                return
            self._send({"op": "stop"})
            proc = self._proc
        if proc is None:
            return
        try:
            proc.wait(timeout=1.2)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=1.2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self._lock:
            self._proc = None

    def pulse(self, ms: int = 220) -> None:
        with self._lock:
            if not self._is_running():
                return
            self._send({"op": "pulse", "ms": int(max(50, ms))})


ACTIVITY_INDICATOR = ActivityIndicator()
