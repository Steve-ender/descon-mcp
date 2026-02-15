from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp import Context
from fastmcp.utilities.types import Image as MCPImage
from mcp.types import SamplingMessage, TextContent
from PIL import Image as PILImage

from descon.config import load_settings
from descon.engines.input_engine import INPUT_ENGINE
from descon.engines.ocr_engine import OCR_ENGINE
from descon.engines.screen_engine import SCREEN_ENGINE
from descon.engines.vision_pipeline import VisionPipeline
from descon.errors import error_code_for_exception
from descon.result import err, now_ms, ok
from descon.safety import assert_can_run
from descon.state import STATE
from descon.tools.recording_tools import maybe_record

_pipeline = VisionPipeline(OCR_ENGINE)


def _err_code(e: Exception) -> str:
    typed = error_code_for_exception(e, default="")
    if typed:
        return typed
    msg = str(e).lower()
    if "foreground mismatch" in msg:
        return "foreground_mismatch"
    if "timed out" in msg:
        return "timeout"
    return "runtime_error"


async def _read_text_host_model(
    image_path: str,
    ctx: Context,
    prompt: str | None = None,
) -> dict[str, Any]:
    model_prompt = prompt or (
        "Read all visible text from this screenshot. "
        "Preserve line breaks. Return only extracted text."
    )
    image_content = MCPImage(path=image_path).to_image_content()
    result = await ctx.sample(
        messages=[
            SamplingMessage(
                role="user",
                content=[
                    TextContent(type="text", text=model_prompt),
                    image_content,
                ],
            )
        ],
        temperature=0.0,
        max_tokens=1800,
    )
    text = result.text or ""
    return {"text": text, "chars": len(text), "backend": "host_model", "confidence": None}


def register_vision_tools(mcp: FastMCP) -> None:
    """Core: desktop_screenshot + desktop_read_text."""

    @mcp.tool(
        description=(
            "Take a screenshot of the screen and return it as an inline image. "
            "The LLM will see the screenshot directly. "
            "Use monitor_index to select which monitor (1=primary). "
            "Optionally pass left/top/width/height to capture a specific region."
        ),
    )
    def desktop_screenshot(
        path: str | None = None,
        monitor_index: int = 1,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        session_id: str | None = None,
    ) -> dict | list:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            region = None
            if None not in (left, top, width, height):
                region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
            data = SCREEN_ENGINE.capture(path=path, monitor_index=monitor_index, region=region)
            maybe_record(
                "desktop_screenshot",
                {
                    "path": path,
                    "monitor_index": monitor_index,
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                },
                data,
                session_id=resolved_session_id,
            )
            metadata = {k: v for k, v in data.items() if k != "base64_png"}
            metadata["dpi_scale"] = SCREEN_ENGINE.dpi_scale()
            result = ok("desktop_screenshot", data=metadata, started_ms=started)
            image = MCPImage(path=data["path"])
            return [result, image]
        except Exception as e:
            return err("desktop_screenshot", str(e), started_ms=started, code=_err_code(e))

    @mcp.tool(
        description=(
            "Extract text via OCR from an image file or directly from a screen region. "
            "Provide image_path for an existing image, OR left/top/width/height to capture "
            "a screen region and OCR it in one call. "
            "backend: auto, auto_with_host, host_model, local_model, tesseract."
        ),
    )
    async def desktop_read_text(
        image_path: str | None = None,
        backend: str = "auto",
        lang: str = "eng",
        prompt: str | None = None,
        stabilization_attempts: int = 3,
        min_consensus: int = 2,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        monitor_index: int = 1,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)

            # Resolve image path: use provided path, or capture a screen region
            actual_image_path = image_path
            if not actual_image_path or not actual_image_path.strip():
                if None not in (left, top, width, height):
                    region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
                    shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index, region=region)
                    actual_image_path = str(shot["path"])
                else:
                    return err(
                        "desktop_read_text",
                        "Provide image_path or screen region (left, top, width, height)",
                        started_ms=started,
                        code="validation_error",
                    )

            selected = backend.strip().lower()
            if selected == "rapidocr":
                selected = "local_model"

            if selected not in {"auto", "auto_with_host", "host_model", "local_model", "tesseract"}:
                if settings.best_effort_ocr:
                    return ok(
                        "desktop_read_text",
                        data={"text": "", "chars": 0, "backend": "none", "warning": f"Unsupported backend: {backend}"},
                        started_ms=started,
                    )
                return err(
                    "desktop_read_text",
                    f"Unsupported backend: {backend}",
                    hint="Use one of: auto, auto_with_host, host_model, local_model, tesseract",
                    started_ms=started,
                )

            failures: list[str] = []

            if selected in {"host_model", "auto_with_host"}:
                if not settings.enable_host_ocr:
                    failures.append("host_model blocked by policy (NOVAFORGE_ENABLE_HOST_OCR=false)")
                    if selected == "host_model":
                        raise RuntimeError("host_model OCR is disabled by local policy")
                elif ctx is None:
                    failures.append("host_model unavailable: no MCP context")
                else:
                    try:
                        data = await _read_text_host_model(image_path=actual_image_path, ctx=ctx, prompt=prompt)
                        if selected == "host_model" or data["chars"] > 0:
                            return ok("desktop_read_text", data=data, started_ms=started)
                    except Exception as e:
                        failures.append(f"host_model failed: {e}")
                        if selected == "host_model":
                            raise

            if selected in {"local_model", "tesseract", "auto", "auto_with_host"}:
                stable_backend = selected if selected != "auto_with_host" else "auto"
                data = _pipeline.read_text_consensus(
                    image_path=actual_image_path,
                    backend=stable_backend,
                    lang=lang,
                    attempts=max(1, int(stabilization_attempts)),
                    min_consensus=max(1, int(min_consensus)),
                    best_effort_ocr=settings.best_effort_ocr,
                    fallback_notes=failures,
                )
                return ok("desktop_read_text", data=data, started_ms=started)

            if settings.best_effort_ocr:
                return ok(
                    "desktop_read_text",
                    data={"text": "", "chars": 0, "backend": "none", "warning": "No OCR backend produced output"},
                    started_ms=started,
                )
            raise RuntimeError("No OCR backend produced output")
        except Exception as e:
            if settings.best_effort_ocr:
                return ok(
                    "desktop_read_text",
                    data={"text": "", "chars": 0, "backend": "none", "warning": str(e)},
                    started_ms=started,
                )
            return err(
                "desktop_read_text",
                str(e),
                hint=(
                    "Use backend=auto for fallback chain. "
                    "For host_model, set NOVAFORGE_ENABLE_HOST_OCR=true and ensure client supports MCP sampling; "
                    "for local fallback install rapidocr/tesseract."
                ),
                started_ms=started,
                code=_err_code(e),
            )

    @mcp.tool(
        description=(
            "Read the RGB color of a pixel at screen coordinates (x, y). "
            "Returns {r, g, b, hex, x, y}. "
            "Use monitor_index to select which monitor (1=primary)."
        ),
    )
    def desktop_pixel(
        x: int,
        y: int,
        monitor_index: int = 1,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            region = {"left": int(x), "top": int(y), "width": 1, "height": 1}
            shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index, region=region)
            img = PILImage.open(str(shot["path"])).convert("RGB")
            pixel = img.getpixel((0, 0))
            r, g, b = pixel[0], pixel[1], pixel[2]
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            data = {"r": r, "g": g, "b": b, "hex": hex_color, "x": x, "y": y, "monitor_index": monitor_index}
            maybe_record("desktop_pixel", {"x": x, "y": y, "monitor_index": monitor_index}, data, session_id=resolved_session_id)
            return ok("desktop_pixel", data=data, started_ms=started)
        except Exception as e:
            return err("desktop_pixel", str(e), started_ms=started, code=_err_code(e))


def register_vision_admin_tools(mcp: FastMCP) -> None:
    """Admin: desktop_find_template only."""

    @mcp.tool(
        description=(
            "Find a template image on screen using OpenCV template matching. "
            "Returns match coordinates and confidence scores. "
            "Set click=true to click the best match (retries up to max_attempts if not found immediately)."
        ),
    )
    def desktop_find_template(
        template_path: str,
        monitor_index: int = 1,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        threshold: float = 0.85,
        max_results: int = 5,
        grayscale: bool = True,
        click: bool = False,
        button: str = "left",
        clicks: int = 1,
        max_attempts: int = 3,
        wait_between_attempts_ms: int = 200,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            region = None
            if None not in (left, top, width, height):
                region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}

            if not click:
                data = SCREEN_ENGINE.find_template(
                    template_path=template_path,
                    monitor_index=monitor_index,
                    region=region,
                    threshold=threshold,
                    max_results=max_results,
                    grayscale=grayscale,
                )
                maybe_record(
                    "desktop_find_template",
                    {"template_path": template_path, "monitor_index": monitor_index, "threshold": threshold},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_find_template", data=data, started_ms=started)

            attempts: list[dict[str, Any]] = []
            for i in range(max(1, max_attempts)):
                data = SCREEN_ENGINE.find_template(
                    template_path=template_path,
                    monitor_index=monitor_index,
                    threshold=threshold,
                    max_results=1,
                    grayscale=True,
                )
                attempts.append({"attempt": i + 1, "found": data["count"] > 0, "best": data.get("best")})
                if data["count"] > 0:
                    best = data["best"]
                    click_result = INPUT_ENGINE.click(
                        x=int(best["center_x"]),
                        y=int(best["center_y"]),
                        button=button,
                        clicks=clicks,
                    )
                    maybe_record(
                        "desktop_find_template",
                        {"template_path": template_path, "click": True, "button": button, "clicks": clicks},
                        {"click": click_result, "match": best},
                        session_id=resolved_session_id,
                    )
                    return ok(
                        "desktop_find_template",
                        data={"click": click_result, "match": best, "attempts": attempts, "threshold": threshold},
                        started_ms=started,
                    )
                INPUT_ENGINE.sleep(wait_between_attempts_ms)
            if settings.best_effort_vision:
                return ok(
                    "desktop_find_template",
                    data={
                        "matched": False,
                        "attempts": attempts,
                        "threshold": threshold,
                        "warning": f"No match >= {threshold} after {max_attempts} attempts",
                    },
                    started_ms=started,
                )
            return err(
                "desktop_find_template",
                "Template not found",
                hint=f"No match >= {threshold} after {max_attempts} attempts",
                started_ms=started,
            )
        except Exception as e:
            return err("desktop_find_template", str(e), started_ms=started, code=_err_code(e))
