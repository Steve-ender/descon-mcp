from __future__ import annotations

import time
from typing import Any

from fastmcp import FastMCP

from descon.config import load_settings
from descon.engines.input_engine import INPUT_ENGINE
from descon.engines.ocr_engine import OCR_ENGINE
from descon.engines.screen_engine import SCREEN_ENGINE
from descon.engines.window_engine import WINDOW_ENGINE
from descon.result import err, now_ms, ok
from descon.safety import assert_can_run
from descon.state import STATE
from descon.tools.recording_tools import maybe_record


def _region_from_box(left: int | None, top: int | None, width: int | None, height: int | None) -> dict[str, int] | None:
    if None not in (left, top, width, height):
        return {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
    return None


def _err_code(e: Exception) -> str:
    msg = str(e).lower()
    if "requires non-empty text_query" in msg:
        return "validation_error"
    if "privilege mismatch" in msg or "administrator" in msg:
        return "permission_denied"
    if "foreground mismatch" in msg:
        return "foreground_mismatch"
    if "foreground refocus failed" in msg:
        return "foreground_refocus_failed"
    if "foreground guard requires a window target" in msg:
        return "foreground_unresolvable_target"
    if "timed out" in msg:
        return "timeout"
    if "not found" in msg or "no window matched" in msg:
        return "not_found"
    if "allowlist" in msg:
        return "policy_blocked"
    if "unsupported" in msg:
        return "unsupported_action"
    return "runtime_error"


def _click_template(template_path: str, monitor_index: int, threshold: float, button: str, clicks: int) -> dict[str, Any]:
    found = SCREEN_ENGINE.find_template(
        template_path=template_path,
        monitor_index=monitor_index,
        threshold=threshold,
        max_results=1,
        grayscale=True,
    )
    if found["count"] < 1 or not found.get("best"):
        raise RuntimeError("Template fallback could not find match")
    best = found["best"]
    click = INPUT_ENGINE.click(
        x=int(best["center_x"]),
        y=int(best["center_y"]),
        button=button,
        clicks=clicks,
    )
    return {"strategy": "template", "match": best, "click": click}


def _click_ocr_text(
    text_query: str,
    monitor_index: int,
    region: dict[str, int] | None,
    button: str,
    clicks: int,
    near_x: int | None = None,
    near_y: int | None = None,
) -> dict[str, Any]:
    shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index, region=region)
    ranked = OCR_ENGINE.select_text_target_local_model(
        image_path=str(shot["path"]),
        query=text_query,
        near_x=near_x,
        near_y=near_y,
    )
    if ranked["count"] < 1 or not ranked.get("best"):
        raise RuntimeError(f"OCR fallback could not find text: {text_query}")
    best = ranked["best"]
    offset_left = int(region["left"]) if region and "left" in region else 0
    offset_top = int(region["top"]) if region and "top" in region else 0
    click = INPUT_ENGINE.click(
        x=int(best["center_x"] + offset_left),
        y=int(best["center_y"] + offset_top),
        button=button,
        clicks=clicks,
    )
    return {"strategy": "ocr_text", "match": best, "ranked": ranked["matches"], "click": click, "screenshot": shot}


def _check_condition(
    condition: str,
    window_title_regex: str | None,
    window_handle: int | None,
    window_pid: int | None,
    title: str | None,
    auto_id: str | None,
    control_type: str | None,
    found_index: int,
    text_query: str | None,
) -> tuple[bool, dict[str, Any]]:
    cond = condition.strip().lower()
    if cond == "gone":
        try:
            info = WINDOW_ENGINE.snapshot_element(
                window_title_regex=window_title_regex,
                window_handle=window_handle,
                window_pid=window_pid,
                title=title,
                auto_id=auto_id,
                control_type=control_type,
                found_index=found_index,
            )
            return False, {"exists": True, "snapshot": info}
        except Exception:
            return True, {"exists": False}

    info = WINDOW_ENGINE.snapshot_element(
        window_title_regex=window_title_regex,
        window_handle=window_handle,
        window_pid=window_pid,
        title=title,
        auto_id=auto_id,
        control_type=control_type,
        found_index=found_index,
    )
    if cond == "exists":
        return True, {"snapshot": info}
    if cond == "visible":
        return bool(info.get("visible")), {"snapshot": info}
    if cond == "enabled":
        return bool(info.get("enabled")), {"snapshot": info}
    if cond == "focused":
        return bool(info.get("focused")), {"snapshot": info}
    if cond == "text_contains":
        q = (text_query or "").strip()
        if not q:
            raise RuntimeError("text_contains requires non-empty text_query")
        candidates = [str(x) for x in (info.get("text_candidates") or [])]
        if not candidates:
            candidates = [str(info.get("title", ""))]
        matched = any(q.lower() in c.lower() for c in candidates)
        return matched, {"snapshot": info, "query": q, "candidates": candidates}
    raise RuntimeError(f"Unsupported condition: {condition}")


def _effective_target(
    window_title_regex: str | None,
    window_handle: int | None,
    window_pid: int | None,
    session_id: str | None = None,
) -> tuple[str | None, int | None, int | None]:
    if window_handle is not None or window_pid is not None or (window_title_regex or "").strip():
        return window_title_regex, window_handle, window_pid
    st = STATE.get(session_id=session_id)
    return st.bound_window_title_regex, st.bound_window_handle, st.bound_window_pid


def _foreground_guard(
    *,
    require_foreground_match: bool,
    window_title_regex: str | None,
    window_handle: int | None,
    window_pid: int | None,
    foreground_mismatch_mode: str,
    foreground_retries: int,
    foreground_retry_wait_ms: int,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    if not require_foreground_match:
        return None
    return WINDOW_ENGINE.ensure_foreground_target(
        expected_title_regex=window_title_regex,
        expected_handle=window_handle,
        expected_pid=window_pid,
        mismatch_mode=foreground_mismatch_mode,
        retries=foreground_retries,
        retry_wait_ms=foreground_retry_wait_ms,
    )


def register_action_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Interact with UI elements via Windows UI Automation. "
            "action: query (inspect element properties: title, rect, visible, enabled, focused), "
            "act (perform element_action=click/double_click/right_click/invoke/type/set_value/select/toggle/expand/collapse/scroll_into_view; "
            "falls back to template/OCR if UIA fails), "
            "wait_for (poll until condition=exists/gone/visible/enabled/focused/text_contains is met, timeout_ms=, poll_ms=), "
            "assert (check condition immediately, returns pass/fail)."
        ),
    )
    def desktop_element(
        action: str,
        window_title_regex: str | None = None,
        title: str | None = None,
        auto_id: str | None = None,
        control_type: str | None = None,
        found_index: int = 0,
        window_handle: int | None = None,
        window_pid: int | None = None,
        timeout_ms: int = 4000,
        element_action: str | None = None,
        text: str | None = None,
        clear_first: bool = False,
        allow_fallback: bool = True,
        require_foreground_match: bool = True,
        foreground_mismatch_mode: str = "refocus_and_retry",
        foreground_retries: int = 3,
        foreground_retry_wait_ms: int = 150,
        template_path: str | None = None,
        text_query: str | None = None,
        monitor_index: int = 1,
        threshold: float = 0.85,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        button: str = "left",
        clicks: int = 1,
        interval_ms: int = 0,
        condition: str = "exists",
        poll_ms: int = 200,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        act = action.strip().lower()

        if act == "query":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                target_regex, target_handle, target_pid = _effective_target(
                    window_title_regex=window_title_regex,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    session_id=resolved_session_id,
                )
                data = WINDOW_ENGINE.snapshot_element(
                    window_title_regex=target_regex,
                    title=title,
                    auto_id=auto_id,
                    control_type=control_type,
                    found_index=found_index,
                    window_handle=target_handle,
                    window_pid=target_pid,
                    timeout_ms=timeout_ms,
                )
                maybe_record(
                    "desktop_element",
                    {
                        "action": "query",
                        "window_title_regex": target_regex,
                        "title": title,
                        "auto_id": auto_id,
                        "control_type": control_type,
                        "found_index": found_index,
                        "timeout_ms": timeout_ms,
                    },
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_element", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_element", str(e), started_ms=started, code=_err_code(e))

        elif act == "act":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                ea = (element_action or "click").strip().lower()
                target_regex, target_handle, target_pid = _effective_target(
                    window_title_regex=window_title_regex,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    session_id=resolved_session_id,
                )
                fg_pre = _foreground_guard(
                    require_foreground_match=require_foreground_match,
                    window_title_regex=target_regex,
                    window_handle=target_handle,
                    window_pid=target_pid,
                    foreground_mismatch_mode=foreground_mismatch_mode,
                    foreground_retries=foreground_retries,
                    foreground_retry_wait_ms=foreground_retry_wait_ms,
                    session_id=resolved_session_id,
                )
                region = _region_from_box(left, top, width, height)
                failures: list[str] = []
                try:
                    data = WINDOW_ENGINE.perform_element_action(
                        action=ea,
                        window_title_regex=target_regex,
                        title=title,
                        auto_id=auto_id,
                        control_type=control_type,
                        found_index=found_index,
                        text=text,
                        clear_first=clear_first,
                        window_handle=target_handle,
                        window_pid=target_pid,
                        timeout_ms=timeout_ms,
                    )
                    fg_post = _foreground_guard(
                        require_foreground_match=require_foreground_match,
                        window_title_regex=target_regex,
                        window_handle=target_handle,
                        window_pid=target_pid,
                        foreground_mismatch_mode=foreground_mismatch_mode,
                        foreground_retries=foreground_retries,
                        foreground_retry_wait_ms=foreground_retry_wait_ms,
                        session_id=resolved_session_id,
                    )
                    data["fallback_notes"] = failures
                    if fg_pre is not None or fg_post is not None:
                        data["foreground"] = {"pre": fg_pre, "post": fg_post}
                    maybe_record(
                        "desktop_element",
                        {
                            "action": "act",
                            "element_action": ea,
                            "window_title_regex": target_regex,
                            "title": title,
                            "auto_id": auto_id,
                            "control_type": control_type,
                            "found_index": found_index,
                            "text": text,
                            "clear_first": clear_first,
                        },
                        data,
                        session_id=resolved_session_id,
                    )
                    return ok("desktop_element", data=data, started_ms=started)
                except Exception as e:
                    failures.append(f"uia failed: {e}")

                if not allow_fallback or ea not in {"click", "left_click", "double_click", "right_click", "type", "clear_type"}:
                    raise RuntimeError("; ".join(failures))

                clicks_effective = clicks
                if ea == "double_click":
                    clicks_effective = 2
                click_button = "right" if ea == "right_click" else button

                if template_path:
                    fallback = _click_template(
                        template_path=template_path,
                        monitor_index=monitor_index,
                        threshold=threshold,
                        button=click_button,
                        clicks=clicks_effective,
                    )
                elif text_query:
                    fallback = _click_ocr_text(
                        text_query=text_query,
                        monitor_index=monitor_index,
                        region=region,
                        button=click_button,
                        clicks=clicks_effective,
                        near_x=int(region["width"] / 2) if region else None,
                        near_y=int(region["height"] / 2) if region else None,
                    )
                else:
                    raise RuntimeError("; ".join(failures))

                if ea in {"type", "clear_type"}:
                    typed = INPUT_ENGINE.type_text(text=text or "", interval_ms=interval_ms)
                    fallback["typed"] = typed
                fg_post = _foreground_guard(
                    require_foreground_match=require_foreground_match,
                    window_title_regex=target_regex,
                    window_handle=target_handle,
                    window_pid=target_pid,
                    foreground_mismatch_mode=foreground_mismatch_mode,
                    foreground_retries=foreground_retries,
                    foreground_retry_wait_ms=foreground_retry_wait_ms,
                    session_id=resolved_session_id,
                )
                fallback["fallback_notes"] = failures
                if fg_pre is not None or fg_post is not None:
                    fallback["foreground"] = {"pre": fg_pre, "post": fg_post}
                maybe_record(
                    "desktop_element",
                    {
                        "action": "act",
                        "element_action": ea,
                        "window_title_regex": target_regex,
                        "title": title,
                        "auto_id": auto_id,
                        "control_type": control_type,
                        "found_index": found_index,
                        "text": text,
                        "allow_fallback": allow_fallback,
                        "template_path": template_path,
                        "text_query": text_query,
                    },
                    fallback,
                    session_id=resolved_session_id,
                )
                return ok("desktop_element", data=fallback, started_ms=started)
            except Exception as e:
                return err("desktop_element", str(e), started_ms=started, code=_err_code(e))

        elif act == "wait_for":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                target_regex, target_handle, target_pid = _effective_target(
                    window_title_regex=window_title_regex,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    session_id=resolved_session_id,
                )
                if condition.strip().lower() == "text_contains" and not (text_query or "").strip():
                    return err(
                        "desktop_element",
                        "text_contains requires non-empty text_query",
                        started_ms=started,
                        code="validation_error",
                    )
                deadline = time.time() + max(0, timeout_ms) / 1000.0
                last_detail: dict[str, Any] = {}
                attempts = 0
                while True:
                    attempts += 1
                    try:
                        passed, detail = _check_condition(
                            condition=condition,
                            window_title_regex=target_regex,
                            window_handle=target_handle,
                            window_pid=target_pid,
                            title=title,
                            auto_id=auto_id,
                            control_type=control_type,
                            found_index=found_index,
                            text_query=text_query,
                        )
                        last_detail = detail
                        if passed:
                            data = {"condition": condition, "passed": True, "attempts": attempts, "detail": detail}
                            maybe_record(
                                "desktop_element",
                                {
                                    "action": "wait_for",
                                    "condition": condition,
                                    "window_title_regex": target_regex,
                                    "title": title,
                                    "auto_id": auto_id,
                                    "control_type": control_type,
                                    "text_query": text_query,
                                    "timeout_ms": timeout_ms,
                                    "poll_ms": poll_ms,
                                },
                                data,
                                session_id=resolved_session_id,
                            )
                            return ok("desktop_element", data=data, started_ms=started)
                    except Exception as probe_error:
                        last_detail = {"probe_error": str(probe_error)}
                    if time.time() >= deadline:
                        return err(
                            "desktop_element",
                            f"Condition timed out: {condition}",
                            hint=str(last_detail),
                            started_ms=started,
                        )
                    INPUT_ENGINE.sleep(max(10, poll_ms))
            except Exception as e:
                return err("desktop_element", str(e), started_ms=started, code=_err_code(e))

        elif act == "assert":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                target_regex, target_handle, target_pid = _effective_target(
                    window_title_regex=window_title_regex,
                    window_handle=window_handle,
                    window_pid=window_pid,
                    session_id=resolved_session_id,
                )
                passed, detail = _check_condition(
                    condition=condition,
                    window_title_regex=target_regex,
                    window_handle=target_handle,
                    window_pid=target_pid,
                    title=title,
                    auto_id=auto_id,
                    control_type=control_type,
                    found_index=found_index,
                    text_query=text_query,
                )
                if not passed:
                    return err("desktop_element", f"Assertion failed: {condition}", hint=str(detail), started_ms=started)
                data = {"condition": condition, "passed": True, "detail": detail}
                maybe_record(
                    "desktop_element",
                    {
                        "action": "assert",
                        "condition": condition,
                        "window_title_regex": target_regex,
                        "title": title,
                        "auto_id": auto_id,
                        "control_type": control_type,
                        "text_query": text_query,
                    },
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_element", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_element", str(e), started_ms=started, code=_err_code(e))

        else:
            return err(
                "desktop_element",
                f"Unknown action: {action}",
                hint="Use one of: query, act, wait_for, assert",
                started_ms=started,
                code="validation_error",
            )
