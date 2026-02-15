from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.utilities.types import Image as MCPImage

from descon.config import load_settings
from descon.engines.input_engine import INPUT_ENGINE
from descon.engines.ocr_engine import OCR_ENGINE
from descon.engines.screen_engine import SCREEN_ENGINE
from descon.engines.transaction_engine import TRANSACTION_ENGINE
from descon.engines.uia_tree_engine import UIA_TREE_ENGINE
from descon.engines.window_engine import WINDOW_ENGINE
from descon.result import err, now_ms, ok
from descon.safety import assert_can_run
from descon.state import STATE


def register_inspect_tools(mcp: FastMCP) -> None:

    @mcp.tool(description="Get a comprehensive snapshot of the desktop: visible windows, cursor position, monitors (with DPI scale), transaction state, and a screenshot returned inline so you can SEE the screen. Set include_elements=True for structured accessibility-tree data (interactive elements, scrollable areas). Set annotate=True to overlay numbered bounding boxes on the screenshot.")
    def desktop_observe(
        include_screenshot: bool = True,
        include_ocr_preview: bool = False,
        include_elements: bool = False,
        use_dom: bool = False,
        annotate: bool = False,
        monitor_index: int = 1,
        max_windows: int = 20,
        ocr_preview_chars: int = 500,
        session_id: str | None = None,
    ) -> dict | list:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)

            windows = WINDOW_ENGINE.list_windows(only_visible=True)[: max(1, int(max_windows))]
            cursor = INPUT_ENGINE.position()
            tx = TRANSACTION_ENGINE.status(session_id=resolved_session_id)
            monitors = SCREEN_ENGINE.monitors()
            dpi_scale = SCREEN_ENGINE.dpi_scale()

            shot: dict[str, Any] | None = None
            ocr_preview: dict[str, Any] | None = None
            image_path: str | None = None
            if include_screenshot:
                shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index, region=None)
                image_path = shot.get("path")
                shot = {k: v for k, v in shot.items() if k != "base64_png"}
                if include_ocr_preview:
                    ocr = OCR_ENGINE.read_text_local_model(image_path)
                    text = str(ocr.get("text", ""))[: max(0, int(ocr_preview_chars))]
                    ocr_preview = {
                        "backend": ocr.get("backend"),
                        "chars_total": ocr.get("chars", 0),
                        "chars_preview": len(text),
                        "text_preview": text,
                    }

            # Structured accessibility-tree snapshot
            elements_data: dict[str, Any] = {}
            snapshot_obj = None
            if include_elements:
                snapshot_obj = UIA_TREE_ENGINE.snapshot(windows, use_dom=use_dom)
                elements_data["interactive_elements"] = snapshot_obj.interactive_to_text()
                elements_data["scrollable_areas"] = snapshot_obj.scrollable_to_text()
                elements_data["interactive_count"] = len(snapshot_obj.interactive)
                elements_data["scrollable_count"] = len(snapshot_obj.scrollable)
                elements_data["tree_elapsed_ms"] = snapshot_obj.elapsed_ms
                if use_dom and snapshot_obj.dom_text:
                    elements_data["dom_text"] = "\n".join(snapshot_obj.dom_text)

            # Annotated screenshot overlay
            if annotate and include_elements and include_screenshot and image_path and snapshot_obj:
                annotated_path = SCREEN_ENGINE.annotate_screenshot(image_path, snapshot_obj.interactive)
                if annotated_path:
                    image_path = annotated_path
                    elements_data["annotated_screenshot"] = annotated_path

            data = {
                "cursor": cursor,
                "windows": windows,
                "window_count": len(windows),
                "focused_window_hint": STATE.get(session_id=resolved_session_id).focused_window_title,
                "monitors": monitors,
                "monitor_count": len(monitors),
                "dpi_scale": dpi_scale,
                "screenshot": shot,
                "ocr_preview": ocr_preview,
                "transaction": tx,
                **elements_data,
            }
            result = ok("desktop_observe", data=data, started_ms=started)
            if image_path:
                return [result, MCPImage(path=image_path)]
            return result
        except Exception as e:
            return err("desktop_observe", str(e), started_ms=started, code="observe_failed")
