from __future__ import annotations

import argparse
import ctypes
import json
import queue
import sys
import threading
import time

import tkinter as tk
from mss import mss


SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


def _virtual_screen_rect() -> tuple[int, int, int, int]:
    u32 = ctypes.windll.user32
    left = int(u32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(u32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(u32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(u32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    return left, top, width, height


def _monitor_rects() -> list[tuple[int, int, int, int]]:
    try:
        with mss() as sct:
            # mss monitor[0] is the aggregate desktop. Use physical monitors [1:].
            mons = sct.monitors[1:]
            out: list[tuple[int, int, int, int]] = []
            for m in mons:
                out.append((int(m["left"]), int(m["top"]), int(m["width"]), int(m["height"])))
            if out:
                return out
    except Exception:
        pass
    return [_virtual_screen_rect()]


def _make_clickthrough(hwnd: int) -> None:
    try:
        u32 = ctypes.windll.user32
        style = int(u32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


def _reader_thread(cmd_q: queue.Queue[dict]) -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                cmd_q.put(payload)
        except Exception:
            continue


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--color", default="#00ff88")
    parser.add_argument("--thickness", type=int, default=8)
    args = parser.parse_args()

    color = args.color
    thickness = max(2, int(args.thickness))
    cmd_q: queue.Queue[dict] = queue.Queue()
    threading.Thread(target=_reader_thread, args=(cmd_q,), daemon=True).start()

    root = tk.Tk()
    root.withdraw()
    geometries: list[tuple[int, int, int, int]] = []
    for left, top, width, height in _monitor_rects():
        geometries.extend(
            [
                (left, top, width, thickness),
                (left, top + height - thickness, width, thickness),
                (left, top, thickness, height),
                (left + width - thickness, top, thickness, height),
            ]
        )
    windows: list[tk.Toplevel] = []
    for x, y, w, h in geometries:
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=color)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.attributes("-alpha", 0.0)
        win.update_idletasks()
        _make_clickthrough(int(win.winfo_id()))
        windows.append(win)

    active = False
    pulse_until = 0.0

    def tick() -> None:
        nonlocal active, pulse_until
        now = time.time()
        while True:
            try:
                cmd = cmd_q.get_nowait()
            except queue.Empty:
                break
            op = cmd.get("op")
            if op == "stop":
                try:
                    root.destroy()
                except Exception:
                    pass
                return
            if op == "active":
                active = bool(cmd.get("value"))
            elif op == "pulse":
                pulse_until = max(pulse_until, now + (max(50, int(cmd.get("ms", 220))) / 1000.0))

        alpha = 0.42 if active else 0.0
        if now < pulse_until:
            alpha = 0.80
        for win in windows:
            try:
                win.attributes("-alpha", alpha)
            except Exception:
                pass
        root.after(60, tick)

    root.after(60, tick)
    root.mainloop()


if __name__ == "__main__":
    main()
