from __future__ import annotations

import base64
import io
from typing import Any

from mss import mss
from PIL import Image
import cv2
import numpy as np

from descon.engines.artifact_manager import ARTIFACT_MANAGER
from descon.errors import ArtifactWriteError


def _get_dpi_scale() -> float:
    """Get the system DPI scale factor (1.0 = 100%, 1.5 = 150%, etc.)."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        pass
    try:
        import ctypes
        hdc = ctypes.windll.user32.GetDC(0)
        if not hdc:
            return 1.0
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            return round(dpi / 96.0, 2) if dpi else 1.0
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
    except Exception:
        return 1.0


class ScreenEngine:
    def __init__(self) -> None:
        pass

    @staticmethod
    def dpi_scale() -> float:
        return _get_dpi_scale()

    def monitors(self) -> list[dict[str, Any]]:
        with mss() as sct:
            return [dict(m) for m in sct.monitors]

    def _resolve_monitor(self, monitor_index: int, sct: Any = None) -> dict[str, int]:
        if sct is None:
            with mss() as s:
                monitors = s.monitors
        else:
            monitors = sct.monitors
        idx = int(monitor_index)
        if idx < 0:
            idx = 0
        if idx >= len(monitors):
            idx = max(0, len(monitors) - 1)
        m = monitors[idx]
        return {
            "left": int(m["left"]),
            "top": int(m["top"]),
            "width": int(m["width"]),
            "height": int(m["height"]),
        }

    def capture(self, path: str | None = None, monitor_index: int = 1, region: dict[str, int] | None = None) -> dict[str, Any]:
        with mss() as sct:
            mon = self._resolve_monitor(monitor_index, sct=sct)
            if region:
                mon = {
                    "left": int(region["left"]),
                    "top": int(region["top"]),
                    "width": int(region["width"]),
                    "height": int(region["height"]),
                }

            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

            out = ARTIFACT_MANAGER.resolve_image_output(path=path, prefix="screen_")
            try:
                img.save(str(out))
            except Exception as e:
                raise ArtifactWriteError(
                    f"Failed to write screenshot artifact: {out}",
                    details={"path": str(out), "cause": str(e)},
                ) from e

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            return {"path": str(out), "width": shot.width, "height": shot.height, "base64_png": b64}

    def find_template(
        self,
        template_path: str,
        monitor_index: int = 1,
        region: dict[str, int] | None = None,
        threshold: float = 0.85,
        max_results: int = 5,
        grayscale: bool = True,
    ) -> dict[str, Any]:
        with mss() as sct:
            mon = self._resolve_monitor(monitor_index, sct=sct)
            if region:
                mon = {
                    "left": int(region["left"]),
                    "top": int(region["top"]),
                    "width": int(region["width"]),
                    "height": int(region["height"]),
                }

            shot = sct.grab(mon)
        frame = np.array(shot)
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None:
            return {
                "matches": [],
                "count": 0,
                "best": None,
                "threshold": threshold,
                "warning": f"Template not found or unreadable: {template_path}",
            }

        haystack = frame_bgr
        needle = template
        if grayscale:
            haystack = cv2.cvtColor(haystack, cv2.COLOR_BGR2GRAY)
            needle = cv2.cvtColor(needle, cv2.COLOR_BGR2GRAY)

        th, tw = needle.shape[:2]
        result = cv2.matchTemplate(haystack, needle, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        scores = result[ys, xs]

        matches: list[dict[str, Any]] = []
        for x, y, score in sorted(zip(xs, ys, scores), key=lambda t: float(t[2]), reverse=True):
            left = int(mon["left"] + x)
            top = int(mon["top"] + y)
            width = int(tw)
            height = int(th)
            center_x = left + width // 2
            center_y = top + height // 2
            too_close = False
            for m in matches:
                if abs(m["center_x"] - center_x) < max(6, width // 8) and abs(m["center_y"] - center_y) < max(6, height // 8):
                    too_close = True
                    break
            if too_close:
                continue
            matches.append(
                {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "center_x": center_x,
                    "center_y": center_y,
                    "score": float(score),
                }
            )
            if len(matches) >= max_results:
                break

        best = matches[0] if matches else None
        return {"matches": matches, "count": len(matches), "best": best, "threshold": threshold}

    def annotate_screenshot(self, image_path: str, elements: list) -> str:
        """Draw numbered bounding boxes on a screenshot. Returns path to annotated image."""
        from PIL import ImageDraw, ImageFont

        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except Exception:
            font = ImageFont.load_default()

        colors = [
            "#FF0000", "#00FF00", "#0066FF", "#FF6600", "#9900FF",
            "#00CCCC", "#FF0066", "#66FF00", "#0000CC", "#CC6600",
        ]

        for elem in elements:
            idx = getattr(elem, "index", None)
            bbox = getattr(elem, "bbox", None)
            if idx is None or bbox is None or len(bbox) != 4:
                continue
            color = colors[idx % len(colors)]
            left, top, right, bottom = bbox
            if right <= left or bottom <= top:
                continue
            draw.rectangle([left, top, right, bottom], outline=color, width=2)
            label = str(idx)
            draw.text((left, max(0, top - 16)), label, fill=color, font=font)

        out = ARTIFACT_MANAGER.resolve_image_output(path=None, prefix="annotated_")
        img.save(str(out))
        return str(out)


SCREEN_ENGINE = ScreenEngine()
