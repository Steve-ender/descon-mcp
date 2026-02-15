from __future__ import annotations

import ctypes
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import psutil

from descon.engines.retry_engine import retry_call
from descon.config import load_settings

try:
    from pywinauto import Desktop
except Exception:  # pragma: no cover
    Desktop = None


@dataclass
class WindowTarget:
    title_regex: str | None = None
    window_handle: int | None = None
    window_pid: int | None = None


def _escape_type_keys(text: str) -> str:
    """Escape pywinauto type_keys special characters so they are typed literally."""
    out: list[str] = []
    for ch in text:
        if ch == '{':
            out.append('{{}')
        elif ch == '}':
            out.append('{}}')
        elif ch == '^':
            out.append('{^}')
        elif ch == '+':
            out.append('{+}')
        elif ch == '%':
            out.append('{%}')
        elif ch == '~':
            out.append('{~}')
        elif ch == '(':
            out.append('{(}')
        elif ch == ')':
            out.append('{)}')
        else:
            out.append(ch)
    return ''.join(out)


class WindowEngine:
    def __init__(self, backend: str = "uia") -> None:
        self.backend = backend

    def _desktop(self):
        if Desktop is None:
            raise RuntimeError("pywinauto is not installed or failed to import")
        return Desktop(backend=self.backend)

    @staticmethod
    def _is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    @staticmethod
    def _process_elevation(pid: int) -> str:
        TOKEN_QUERY = 0x0008
        TOKEN_ELEVATION = 20
        h_process = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h_process:
            return "unknown"
        token = ctypes.c_void_p()
        try:
            if not ctypes.windll.advapi32.OpenProcessToken(h_process, TOKEN_QUERY, ctypes.byref(token)):
                return "unknown"
            elevation = ctypes.c_ulong()
            size = ctypes.c_ulong(ctypes.sizeof(elevation))
            ret_len = ctypes.c_ulong()
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

    @staticmethod
    def _normalize_proc_name(name: str) -> str:
        n = (name or "").strip().lower()
        if n.endswith(".exe"):
            n = n[:-4]
        return n

    @staticmethod
    def _text_candidates(wrapper: Any) -> list[str]:
        values: list[str] = []
        for getter in [
            lambda: wrapper.window_text(),
            lambda: wrapper.get_value(),
            lambda: getattr(getattr(wrapper, "element_info", None), "name", ""),
            lambda: getattr(getattr(wrapper, "element_info", None), "automation_id", ""),
        ]:
            try:
                v = getter()
                if isinstance(v, str) and v.strip():
                    values.append(v)
            except Exception:
                pass
        try:
            texts = wrapper.texts()
            if isinstance(texts, list):
                values.extend([str(t) for t in texts if str(t).strip()])
        except Exception:
            pass
        seen: set[str] = set()
        out: list[str] = []
        for v in values:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _windows(self) -> list[Any]:
        return list(self._desktop().windows())

    @staticmethod
    def _hwnd_title(hwnd: int) -> str:
        try:
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(int(hwnd), buf, 512)
            return str(buf.value or "")
        except Exception:
            return ""

    @staticmethod
    def _hwnd_class_name(hwnd: int) -> str:
        try:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(int(hwnd), buf, 256)
            return str(buf.value or "")
        except Exception:
            return ""

    def get_foreground_window(self) -> dict[str, Any]:
        hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        if hwnd <= 0:
            return {
                "handle": None,
                "pid": None,
                "thread_id": None,
                "title": "",
                "class_name": "",
            }
        pid = ctypes.c_ulong(0)
        thread_id = int(ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))
        return {
            "handle": int(hwnd),
            "pid": int(pid.value) if int(pid.value) > 0 else None,
            "thread_id": thread_id,
            "title": self._hwnd_title(hwnd),
            "class_name": self._hwnd_class_name(hwnd),
        }

    def privilege_snapshot(self, pid: int | None = None) -> dict[str, Any]:
        target_pid = pid
        if target_pid is None:
            fg = self.get_foreground_window()
            target_pid = fg.get("pid")
        host_admin = self._is_admin()
        target_elevation = "unknown"
        if target_pid:
            try:
                target_elevation = self._process_elevation(int(target_pid))
            except Exception:
                target_elevation = "unknown"
        return {
            "host_is_admin": host_admin,
            "target_pid": int(target_pid) if target_pid else None,
            "target_elevation": target_elevation,
            "privilege_mismatch_risk": bool(target_elevation == "elevated" and not host_admin),
        }

    @staticmethod
    def _foreground_matches_target(
        foreground: dict[str, Any],
        *,
        expected_handle: int | None,
        expected_pid: int | None,
        expected_title_regex: str | None,
    ) -> tuple[bool, str]:
        fg_handle = foreground.get("handle")
        fg_pid = foreground.get("pid")
        fg_title = str(foreground.get("title") or "")
        if expected_handle is not None:
            if fg_handle == int(expected_handle):
                return True, "handle"
            return False, "handle_mismatch"
        if expected_pid is not None:
            if fg_pid == int(expected_pid):
                return True, "pid"
            return False, "pid_mismatch"
        if expected_title_regex:
            try:
                if re.search(expected_title_regex, fg_title, re.IGNORECASE):
                    return True, "title_regex"
                return False, "title_regex_mismatch"
            except Exception:
                return False, "title_regex_invalid"
        return False, "no_target"

    def ensure_foreground_target(
        self,
        *,
        expected_handle: int | None = None,
        expected_pid: int | None = None,
        expected_title_regex: str | None = None,
        mismatch_mode: str = "refocus_and_retry",
        retries: int = 3,
        retry_wait_ms: int = 150,
        resolve_timeout_ms: int = 4000,
    ) -> dict[str, Any]:
        mode = str(mismatch_mode or "refocus_and_retry").strip().lower()
        if mode not in {"fail", "refocus_and_retry", "warn"}:
            raise RuntimeError(f"Unsupported foreground mismatch mode: {mismatch_mode}")
        if expected_handle is None and expected_pid is None and not (expected_title_regex or "").strip():
            fg = self.get_foreground_window()
            return {
                "ok": True,
                "matched": bool(fg.get("handle")),
                "mode": "warn",
                "attempts": 0,
                "match_reason": "implicit_foreground_target",
                "before": fg,
                "after": fg,
                "expected": {
                    "handle": fg.get("handle"),
                    "pid": fg.get("pid"),
                    "title_regex": None,
                },
                "warning": "No explicit target provided; foreground window used as implicit target",
            }

        before = self.get_foreground_window()
        matched, reason = self._foreground_matches_target(
            before,
            expected_handle=expected_handle,
            expected_pid=expected_pid,
            expected_title_regex=expected_title_regex,
        )
        if matched:
            return {
                "ok": True,
                "matched": True,
                "mode": mode,
                "attempts": 0,
                "match_reason": reason,
                "before": before,
                "after": before,
                "expected": {
                    "handle": expected_handle,
                    "pid": expected_pid,
                    "title_regex": expected_title_regex,
                },
            }
        if mode == "warn":
            return {
                "ok": True,
                "matched": False,
                "mode": mode,
                "attempts": 0,
                "match_reason": reason,
                "before": before,
                "after": before,
                "expected": {
                    "handle": expected_handle,
                    "pid": expected_pid,
                    "title_regex": expected_title_regex,
                },
                "warning": "Foreground mismatch tolerated by warn mode",
            }
        if mode == "fail":
            raise RuntimeError(
                f"Foreground mismatch ({reason}). expected={expected_handle or expected_pid or expected_title_regex}, "
                f"actual={before}"
            )

        attempts = 0
        last = before
        for attempts in range(1, max(1, int(retries)) + 1):
            target = self.resolve_window(
                title_regex=expected_title_regex,
                window_handle=expected_handle,
                window_pid=expected_pid,
                timeout_ms=resolve_timeout_ms,
            )
            try:
                target.set_focus()
            except Exception:
                pass
            time.sleep(max(10, int(retry_wait_ms)) / 1000.0)
            current = self.get_foreground_window()
            last = current
            now_match, now_reason = self._foreground_matches_target(
                current,
                expected_handle=expected_handle,
                expected_pid=expected_pid,
                expected_title_regex=expected_title_regex,
            )
            if now_match:
                return {
                    "ok": True,
                    "matched": True,
                    "mode": mode,
                    "attempts": attempts,
                    "match_reason": now_reason,
                    "before": before,
                    "after": current,
                    "expected": {
                        "handle": expected_handle,
                        "pid": expected_pid,
                        "title_regex": expected_title_regex,
                    },
                }

        raise RuntimeError(
            f"Foreground refocus failed after {attempts} attempts. "
            f"expected={expected_handle or expected_pid or expected_title_regex}, last={last}"
        )

    def _window_from_handle(self, window_handle: int) -> Any:
        for w in self._windows():
            try:
                if int(w.handle) == int(window_handle):
                    return w
            except Exception:
                continue
        raise RuntimeError(f"No window matched handle: {window_handle}")

    def _window_from_foreground(self) -> Any:
        fg = self.get_foreground_window()
        h = fg.get("handle")
        if h is None:
            raise RuntimeError("No foreground window is available")
        return self._window_from_handle(int(h))

    def _window_from_pid(self, pid: int) -> Any:
        pid_set = {int(pid)}
        try:
            root = psutil.Process(int(pid))
            for child in root.children(recursive=True):
                pid_set.add(int(child.pid))
        except Exception:
            pass
        candidates: list[Any] = []
        for w in self._windows():
            try:
                w_pid = int(w.process_id())
            except Exception:
                continue
            if w_pid not in pid_set:
                continue
            try:
                if not (w.window_text() or "").strip():
                    continue
            except Exception:
                continue
            candidates.append(w)
        if not candidates:
            raise RuntimeError(f"No top-level window found for pid: {pid}")
        # Prefer visible windows first.
        candidates.sort(key=lambda x: 0 if bool(getattr(x, "is_visible", lambda: True)()) else 1)
        return candidates[0]

    def _window_from_title_regex(self, title_regex: str) -> Any:
        pattern = re.compile(title_regex, re.IGNORECASE)
        for w in self._windows():
            try:
                title = w.window_text() or ""
            except Exception:
                title = ""
            if pattern.search(title):
                return w
        raise RuntimeError(f"No window matched regex: {title_regex}")

    def resolve_window(
        self,
        *,
        title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        timeout_ms: int = 5000,
    ) -> Any:
        settings = load_settings()
        if window_handle is None and window_pid is None and not (title_regex or "").strip():
            if settings.window_resolve_fallback_foreground:
                return self._window_from_foreground()
            raise RuntimeError("Window target is required (window_handle, window_pid, or title_regex)")

        def _probe() -> Any:
            if window_handle is not None:
                return self._window_from_handle(window_handle)
            if window_pid is not None:
                return self._window_from_pid(window_pid)
            return self._window_from_title_regex(str(title_regex))

        try:
            return retry_call(_probe, timeout_ms=timeout_ms, interval_ms=150)
        except Exception:
            if settings.window_resolve_fallback_foreground:
                return self._window_from_foreground()
            raise

    def _ensure_window_ready(self, window: Any, timeout_ms: int = 3000, ensure_focus: bool = True) -> None:
        settings = load_settings()

        def _revive() -> None:
            for op in ["restore", "maximize", "set_focus"]:
                try:
                    fn = getattr(window, op, None)
                    if callable(fn):
                        fn()
                except Exception:
                    pass

        def _probe() -> None:
            try:
                visible = bool(window.is_visible())
            except Exception:
                visible = True
            if not visible:
                _revive()
                try:
                    visible = bool(window.is_visible())
                except Exception:
                    visible = True
                if not visible:
                    if settings.strict_window_visibility:
                        raise RuntimeError("Window is not visible")
            if ensure_focus:
                pid = None
                try:
                    pid = int(window.process_id())
                except Exception:
                    pid = None
                if pid:
                    target_elev = self._process_elevation(pid)
                    if target_elev == "elevated" and not self._is_admin():
                        if settings.strict_privilege_check:
                            raise RuntimeError(
                                "Privilege mismatch: target window is elevated while automation host is standard. "
                                "Relaunch automation host as administrator."
                            )
                try:
                    window.set_focus()
                except Exception:
                    pass
            return None

        retry_call(_probe, timeout_ms=timeout_ms, interval_ms=120)

    def _locator_candidates(
        self,
        *,
        title: str | None,
        auto_id: str | None,
        control_type: str | None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        if auto_id:
            if control_type:
                candidates.append({"auto_id": auto_id, "control_type": control_type})
            candidates.append({"auto_id": auto_id})
        if title:
            if control_type:
                candidates.append({"title": title, "control_type": control_type})
            candidates.append({"title": title})
        if control_type:
            candidates.append({"control_type": control_type})
        if not candidates:
            candidates.append({})
        return candidates

    def _resolve_element(
        self,
        *,
        title_regex: str | None,
        window_handle: int | None,
        window_pid: int | None,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        found_index: int = 0,
        timeout_ms: int = 4000,
    ) -> tuple[Any, Any]:
        window = self.resolve_window(
            title_regex=title_regex,
            window_handle=window_handle,
            window_pid=window_pid,
            timeout_ms=timeout_ms,
        )
        self._ensure_window_ready(window, timeout_ms=min(timeout_ms, 3000))
        index = max(0, int(found_index))

        def _probe() -> Any:
            locator_error: str = "Element not found"
            for locator in self._locator_candidates(title=title, auto_id=auto_id, control_type=control_type):
                if not locator:
                    return window.wrapper_object() if hasattr(window, "wrapper_object") else window
                try:
                    matches = window.descendants(**locator)
                except Exception as e:
                    locator_error = f"Locator failed {locator}: {e}"
                    continue
                if not matches:
                    locator_error = f"No matches for locator {locator}"
                    continue
                if index >= len(matches):
                    locator_error = f"found_index={index} is out of range for locator {locator} ({len(matches)} matches)"
                    continue
                candidate = matches[index]
                return candidate.wrapper_object() if hasattr(candidate, "wrapper_object") else candidate
            raise RuntimeError(locator_error)

        wrapper = retry_call(_probe, timeout_ms=timeout_ms, interval_ms=120)
        return window, wrapper

    def locate_element(
        self,
        window_title_regex: str | None = None,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        found_index: int = 0,
        window_handle: int | None = None,
        window_pid: int | None = None,
        timeout_ms: int = 4000,
    ) -> tuple[Any, str]:
        window, wrapper = self._resolve_element(
            title_regex=window_title_regex,
            window_handle=window_handle,
            window_pid=window_pid,
            title=title,
            auto_id=auto_id,
            control_type=control_type,
            found_index=found_index,
            timeout_ms=timeout_ms,
        )
        return wrapper, window.window_text() or ""

    def snapshot_element(
        self,
        window_title_regex: str | None = None,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        found_index: int = 0,
        window_handle: int | None = None,
        window_pid: int | None = None,
        timeout_ms: int = 4000,
    ) -> dict[str, Any]:
        window, wrapper = self._resolve_element(
            title_regex=window_title_regex,
            window_handle=window_handle,
            window_pid=window_pid,
            title=title,
            auto_id=auto_id,
            control_type=control_type,
            found_index=found_index,
            timeout_ms=timeout_ms,
        )
        rect = wrapper.rectangle()
        capabilities = {
            "invoke": hasattr(wrapper, "invoke"),
            "click_input": hasattr(wrapper, "click_input"),
            "set_edit_text": hasattr(wrapper, "set_edit_text"),
            "type_keys": hasattr(wrapper, "type_keys"),
            "toggle": hasattr(wrapper, "toggle"),
            "select": hasattr(wrapper, "select"),
            "expand": hasattr(wrapper, "expand"),
            "collapse": hasattr(wrapper, "collapse"),
            "scroll_into_view": hasattr(wrapper, "scroll_into_view"),
        }
        text_candidates = self._text_candidates(wrapper)
        text = text_candidates[0] if text_candidates else ""
        try:
            visible = bool(wrapper.is_visible())
        except Exception:
            visible = None
        try:
            enabled = bool(wrapper.is_enabled())
        except Exception:
            enabled = None
        try:
            focused = bool(wrapper.has_keyboard_focus())
        except Exception:
            focused = None
        pid = None
        try:
            pid = int(window.process_id())
        except Exception:
            pass
        return {
            "window_title": window.window_text() or "",
            "window_handle": int(window.handle),
            "window_pid": pid,
            "title": text,
            "control_type": getattr(wrapper, "friendly_class_name", lambda: "")(),
            "automation_id": getattr(wrapper.element_info, "automation_id", "") if hasattr(wrapper, "element_info") else "",
            "class_name": getattr(wrapper.element_info, "class_name", "") if hasattr(wrapper, "element_info") else "",
            "visible": visible,
            "enabled": enabled,
            "focused": focused,
            "text_candidates": text_candidates,
            "capabilities": capabilities,
            "rect": {
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "center_x": int((rect.left + rect.right) / 2),
                "center_y": int((rect.top + rect.bottom) / 2),
            },
        }

    def list_windows(self, only_visible: bool = True) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for w in self._windows():
            title = w.window_text()
            if not title:
                continue
            try:
                visible = bool(w.is_visible())
            except Exception:
                visible = True
            if only_visible and not visible:
                continue
            pid = None
            try:
                pid = int(w.process_id())
            except Exception:
                pass
            out.append(
                {
                    "title": title,
                    "handle": int(w.handle),
                    "pid": pid,
                    "visible": visible,
                    "class_name": getattr(w, "class_name", lambda: "")(),
                }
            )
        return out

    def focus_window(
        self,
        title_regex: str,
        timeout_ms: int = 5000,
    ) -> dict[str, Any]:
        w = self.resolve_window(title_regex=title_regex, timeout_ms=timeout_ms)
        self._ensure_window_ready(w, timeout_ms=min(timeout_ms, 3000))
        try:
            pid = int(w.process_id())
        except Exception:
            pid = None
        return {"title": w.window_text() or "", "handle": int(w.handle), "pid": pid}

    def focus_window_by_pid(self, pid: int, timeout_ms: int = 5000) -> dict[str, Any]:
        w = self.resolve_window(window_pid=int(pid), timeout_ms=timeout_ms)
        self._ensure_window_ready(w, timeout_ms=min(timeout_ms, 3000))
        return {"title": w.window_text() or "", "handle": int(w.handle), "pid": int(pid)}

    def focus_window_by_handle(self, window_handle: int, timeout_ms: int = 3000) -> dict[str, Any]:
        w = self.resolve_window(window_handle=int(window_handle), timeout_ms=timeout_ms)
        self._ensure_window_ready(w, timeout_ms=min(timeout_ms, 2000))
        pid = None
        try:
            pid = int(w.process_id())
        except Exception:
            pass
        return {"title": w.window_text() or "", "handle": int(w.handle), "pid": pid}

    def launch_app(
        self,
        command: str,
        shell_mode: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if shell_mode:
            p = subprocess.Popen(
                command, shell=True, cwd=cwd, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return {"pid": p.pid, "command": command, "shell_mode": True, "cwd": cwd}
        parts = shlex.split(command, posix=False)
        if not parts:
            raise RuntimeError("Launch command is empty")
        p = subprocess.Popen(
            parts, shell=False, cwd=cwd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"pid": p.pid, "command": command, "argv": parts, "shell_mode": False, "cwd": cwd}

    def close_app(self, pid: int | None = None, name: str | None = None) -> dict[str, Any]:
        if pid is not None:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except psutil.TimeoutExpired:
                proc.kill()
            return {"pid": pid, "name": proc.name()}
        if name:
            target = self._normalize_proc_name(name)
            closed = []
            terminated: list[psutil.Process] = []
            for proc in psutil.process_iter(["pid", "name"]):
                pname = self._normalize_proc_name(proc.info.get("name") or "")
                if pname == target:
                    proc.terminate()
                    terminated.append(proc)
                    closed.append({"pid": int(proc.info["pid"]), "name": proc.info["name"]})
            for proc in terminated:
                try:
                    proc.wait(timeout=3.0)
                except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                    try:
                        proc.kill()
                    except psutil.NoSuchProcess:
                        pass
            if not closed:
                raise RuntimeError(f"No running process matched: {name}")
            return {"closed": closed}
        raise RuntimeError("Provide pid or name")

    def find_elements(
        self,
        window_title_regex: str | None = None,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        found_index: int = 0,
        window_handle: int | None = None,
        window_pid: int | None = None,
        max_results: int = 25,
        timeout_ms: int = 4000,
    ) -> list[dict[str, Any]]:
        window = self.resolve_window(
            title_regex=window_title_regex,
            window_handle=window_handle,
            window_pid=window_pid,
            timeout_ms=timeout_ms,
        )
        locator_candidates = self._locator_candidates(title=title, auto_id=auto_id, control_type=control_type)
        matches: list[Any] = []
        for locator in locator_candidates:
            if not locator:
                matches = [window.wrapper_object() if hasattr(window, "wrapper_object") else window]
                break
            try:
                maybe = window.descendants(**locator)
            except Exception:
                continue
            if maybe:
                matches = maybe
                break
        if not matches:
            raise RuntimeError("Element not found with given locator")
        start = max(0, int(found_index))
        selected = matches[start : start + max(1, int(max_results))]
        out: list[dict[str, Any]] = []
        for wrapper in selected:
            obj = wrapper.wrapper_object() if hasattr(wrapper, "wrapper_object") else wrapper
            rect = obj.rectangle()
            out.append(
                {
                    "title": getattr(obj, "window_text", lambda: "")(),
                    "control_type": getattr(obj, "friendly_class_name", lambda: "")(),
                    "automation_id": getattr(obj.element_info, "automation_id", "") if hasattr(obj, "element_info") else "",
                    "window_title": window.window_text() or "",
                    "window_handle": int(window.handle),
                    "rect": {
                        "left": rect.left,
                        "top": rect.top,
                        "right": rect.right,
                        "bottom": rect.bottom,
                        "center_x": int((rect.left + rect.right) / 2),
                        "center_y": int((rect.top + rect.bottom) / 2),
                    },
                }
            )
        return out

    def click_element(
        self,
        window_title_regex: str | None = None,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        found_index: int = 0,
        window_handle: int | None = None,
        window_pid: int | None = None,
        timeout_ms: int = 4000,
    ) -> dict[str, Any]:
        wrapper, _ = self.locate_element(
            window_title_regex=window_title_regex,
            title=title,
            auto_id=auto_id,
            control_type=control_type,
            found_index=found_index,
            window_handle=window_handle,
            window_pid=window_pid,
            timeout_ms=timeout_ms,
        )
        rect = wrapper.rectangle()
        center_x = int((rect.left + rect.right) / 2)
        center_y = int((rect.top + rect.bottom) / 2)
        try:
            wrapper.set_focus()
        except Exception:
            pass
        try:
            wrapper.click_input()
            strategy = "uia_click_input"
        except Exception:
            import pyautogui as _pag
            _pag.click(center_x, center_y)
            strategy = "uia_center_fallback"
        return {
            "strategy": strategy,
            "rect": {
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "center_x": center_x,
                "center_y": center_y,
            },
            "title": getattr(wrapper, "window_text", lambda: "")(),
        }

    def type_element(
        self,
        window_title_regex: str | None,
        text: str,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        clear_first: bool = False,
        found_index: int = 0,
        window_handle: int | None = None,
        window_pid: int | None = None,
        timeout_ms: int = 4000,
    ) -> dict[str, Any]:
        wrapper, _ = self.locate_element(
            window_title_regex=window_title_regex,
            title=title,
            auto_id=auto_id,
            control_type=control_type,
            found_index=found_index,
            window_handle=window_handle,
            window_pid=window_pid,
            timeout_ms=timeout_ms,
        )
        wrapper.set_focus()
        if clear_first:
            try:
                wrapper.type_keys("^a{BACKSPACE}", set_foreground=True)
            except Exception:
                pass
        try:
            escaped = _escape_type_keys(text)
            wrapper.type_keys(escaped, with_spaces=True, set_foreground=True)
            strategy = "uia_type_keys"
        except Exception:
            wrapper.set_edit_text(text)
            strategy = "uia_set_edit_text"
        return {"strategy": strategy, "typed_chars": len(text)}

    def perform_element_action(
        self,
        action: str,
        window_title_regex: str | None,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        found_index: int = 0,
        text: str | None = None,
        clear_first: bool = False,
        window_handle: int | None = None,
        window_pid: int | None = None,
        timeout_ms: int = 4000,
    ) -> dict[str, Any]:
        wrapper, _ = self.locate_element(
            window_title_regex=window_title_regex,
            title=title,
            auto_id=auto_id,
            control_type=control_type,
            found_index=found_index,
            window_handle=window_handle,
            window_pid=window_pid,
            timeout_ms=timeout_ms,
        )
        action_name = action.strip().lower()
        wrapper.set_focus()
        if action_name in {"click", "left_click"}:
            wrapper.click_input()
            return {"strategy": "uia_click_input"}
        if action_name == "double_click":
            wrapper.double_click_input()
            return {"strategy": "uia_double_click_input"}
        if action_name in {"right_click", "context_click"}:
            wrapper.right_click_input()
            return {"strategy": "uia_right_click_input"}
        if action_name == "invoke":
            if hasattr(wrapper, "invoke"):
                wrapper.invoke()
                return {"strategy": "uia_invoke"}
            wrapper.click_input()
            return {"strategy": "uia_invoke_fallback_click"}
        if action_name in {"type", "clear_type"}:
            value = text or ""
            if clear_first or action_name == "clear_type":
                try:
                    wrapper.type_keys("^a{BACKSPACE}", set_foreground=True)
                except Exception:
                    pass
            try:
                escaped = _escape_type_keys(value)
                wrapper.type_keys(escaped, with_spaces=True, set_foreground=True)
                return {"strategy": "uia_type_keys", "typed_chars": len(value)}
            except Exception:
                wrapper.set_edit_text(value)
                return {"strategy": "uia_set_edit_text", "typed_chars": len(value)}
        if action_name == "set_value":
            value = text or ""
            wrapper.set_edit_text(value)
            return {"strategy": "uia_set_edit_text", "typed_chars": len(value)}
        if action_name == "select":
            if hasattr(wrapper, "select"):
                wrapper.select()
                return {"strategy": "uia_select"}
            wrapper.click_input()
            return {"strategy": "uia_select_fallback_click"}
        if action_name == "toggle":
            if hasattr(wrapper, "toggle"):
                wrapper.toggle()
                return {"strategy": "uia_toggle"}
            raise RuntimeError("Element does not support toggle")
        if action_name == "expand":
            if hasattr(wrapper, "expand"):
                wrapper.expand()
                return {"strategy": "uia_expand"}
            raise RuntimeError("Element does not support expand")
        if action_name == "collapse":
            if hasattr(wrapper, "collapse"):
                wrapper.collapse()
                return {"strategy": "uia_collapse"}
            raise RuntimeError("Element does not support collapse")
        if action_name == "scroll_into_view":
            if hasattr(wrapper, "scroll_into_view"):
                wrapper.scroll_into_view()
                return {"strategy": "uia_scroll_into_view"}
            if hasattr(wrapper, "iface_scroll_item"):
                wrapper.iface_scroll_item.ScrollIntoView()
                return {"strategy": "uia_iface_scroll_item"}
            raise RuntimeError("Element does not support scroll into view")
        raise RuntimeError(f"Unsupported element action: {action}")


    def element_tree(
        self,
        title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        max_depth: int = 3,
        max_children: int = 25,
        timeout_ms: int = 4000,
    ) -> dict[str, Any]:
        window = self.resolve_window(
            title_regex=title_regex,
            window_handle=window_handle,
            window_pid=window_pid,
            timeout_ms=timeout_ms,
        )

        def _walk(element: Any, depth: int) -> dict[str, Any]:
            obj = element.wrapper_object() if hasattr(element, "wrapper_object") else element
            try:
                title = obj.window_text() or ""
            except Exception:
                title = ""
            try:
                control_type = obj.friendly_class_name()
            except Exception:
                control_type = ""
            auto_id = ""
            try:
                auto_id = getattr(obj.element_info, "automation_id", "") if hasattr(obj, "element_info") else ""
            except Exception:
                pass
            try:
                rect = obj.rectangle()
                rect_dict = {
                    "left": rect.left, "top": rect.top,
                    "right": rect.right, "bottom": rect.bottom,
                    "center_x": int((rect.left + rect.right) / 2),
                    "center_y": int((rect.top + rect.bottom) / 2),
                }
            except Exception:
                rect_dict = None

            node: dict[str, Any] = {
                "title": title,
                "control_type": control_type,
                "automation_id": auto_id,
                "rect": rect_dict,
            }
            if depth < max_depth:
                children: list[dict[str, Any]] = []
                try:
                    kids = element.children()
                    for child in kids[:max_children]:
                        children.append(_walk(child, depth + 1))
                    if len(kids) > max_children:
                        children.append({"truncated": True, "total": len(kids), "shown": max_children})
                except Exception:
                    pass
                node["children"] = children
            return node

        tree = _walk(window, 0)
        pid = None
        try:
            pid = int(window.process_id())
        except Exception:
            pass
        return {
            "window_title": window.window_text() or "",
            "window_handle": int(window.handle),
            "window_pid": pid,
            "max_depth": max_depth,
            "tree": tree,
        }

    def window_control(
        self,
        action: str,
        title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        timeout_ms: int = 4000,
    ) -> dict[str, Any]:
        window = self.resolve_window(
            title_regex=title_regex,
            window_handle=window_handle,
            window_pid=window_pid,
            timeout_ms=timeout_ms,
        )
        act = action.strip().lower()
        if act == "minimize":
            window.minimize()
        elif act == "maximize":
            window.maximize()
        elif act == "restore":
            window.restore()
        elif act == "close":
            window.close()
        elif act == "move":
            if x is None or y is None:
                raise RuntimeError("move requires x and y")
            rect = window.rectangle()
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            window.move_window(x=int(x), y=int(y), width=w, height=h)
        elif act == "resize":
            if width is None or height is None:
                raise RuntimeError("resize requires width and height")
            rect = window.rectangle()
            window.move_window(x=rect.left, y=rect.top, width=int(width), height=int(height))
        elif act == "move_resize":
            if x is None or y is None or width is None or height is None:
                raise RuntimeError("move_resize requires x, y, width, and height")
            window.move_window(x=int(x), y=int(y), width=int(width), height=int(height))
        else:
            raise RuntimeError(f"Unsupported window control action: {action}")
        rect = window.rectangle()
        pid = None
        try:
            pid = int(window.process_id())
        except Exception:
            pass
        return {
            "action": act,
            "title": window.window_text() or "",
            "handle": int(window.handle),
            "pid": pid,
            "rect": {
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            },
        }


WINDOW_ENGINE = WindowEngine(backend="uia")
