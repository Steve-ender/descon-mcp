from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Context
from fastmcp import FastMCP

from novaforge.config import load_settings
from novaforge.paths import artifacts_subdir
from novaforge.result import err, now_ms, ok
from novaforge.safety import assert_can_run
from novaforge.state import STATE
from novaforge.tools.orchestration_tools import execute_plan


def maybe_record(
    action: str,
    params: dict[str, Any],
    data: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> None:
    STATE.recording_append(action=action, params=params, data=data or {}, session_id=session_id)


def _event_to_step(event: dict[str, Any]) -> dict[str, Any] | None:
    action = str(event.get("action", "")).strip().lower()
    params = event.get("params") or {}
    if not isinstance(params, dict):
        return None

    mapping: dict[str, str] = {
        "desktop_move_mouse": "move_mouse",
        "desktop_click": "click",
        "desktop_mouse_down": "mouse_down",
        "desktop_mouse_up": "mouse_up",
        "desktop_drag_to": "drag_to",
        "desktop_type": "type",
        "desktop_hotkey": "hotkey",
        "desktop_key_press": "key_press",
        "desktop_wait": "wait",
        "desktop_focus_window": "focus_window",
        "desktop_focus_window_pid": "focus_window",
        "desktop_focus_window_handle": "focus_window",
        "desktop_focus_guard": "focus_guard",
        "desktop_launch_app": "launch_app",
        "desktop_close_app": "close_app",
        "desktop_screenshot": "screenshot",
        "desktop_find_template": "find_template",
        "desktop_click_template": "click_template",
        "desktop_element_action": "element_action",
        "desktop_wait_for": "wait_for",
        "desktop_assert": "assert",
        "desktop_transaction_start": "transaction_start",
        "desktop_transaction_checkpoint": "transaction_checkpoint",
        "desktop_transaction_rollback_hint": "transaction_rollback_hint",
        "desktop_transaction_end": "transaction_end",
        "desktop_transaction_status": "transaction_status",
        # New consolidated tool names
        "desktop_mouse": "move_mouse",
        "desktop_key": "hotkey",
        "desktop_clipboard": "clipboard",
        "desktop_window": "focus_window",
        "desktop_element": "element_action",
        "desktop_session": "session",
        "desktop_health": "health",
        "desktop_transaction": "transaction_start",
        "desktop_recording": "recording",
    }
    if action == "desktop_element_action":
        element_action = str(params.get("action", "click"))
        out = {k: v for k, v in params.items() if k != "action"}
        out["action"] = "element_action"
        out["element_action"] = element_action
        return out
    if action == "desktop_focus_window_pid":
        out = {"action": "focus_window", "window_pid": params.get("pid")}
        if "timeout_ms" in params:
            out["timeout_ms"] = params.get("timeout_ms")
        return out
    if action == "desktop_focus_window_handle":
        out = {"action": "focus_window", "window_handle": params.get("window_handle")}
        if "timeout_ms" in params:
            out["timeout_ms"] = params.get("timeout_ms")
        return out

    mapped = mapping.get(action)
    if mapped:
        return {"action": mapped, **params}

    if action == "desktop_act":
        plan = params.get("plan")
        if isinstance(plan, list):
            return {"action": "nested_plan", "plan": plan}
    return None


def _to_plan(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        step = _event_to_step(ev)
        if not step:
            continue
        if step.get("action") == "nested_plan":
            nested = step.get("plan")
            if isinstance(nested, list):
                for n in nested:
                    if isinstance(n, dict):
                        out.append(n)
            continue
        out.append(step)
    return out


def _stable_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    action = str(step.get("action", "")).strip().lower()
    normalized["action"] = action

    keep_common = {
        "name",
        "retries",
        "retry_wait_ms",
        "wait_after_ms",
        "monitor_index",
        "region",
        "threshold",
        "button",
        "clicks",
        "time_ms",
        "interval_ms",
        "duration_ms",
    }
    for key in keep_common:
        if key in step:
            normalized[key] = step[key]

    action_fields: dict[str, set[str]] = {
        "move_mouse": {
            "x", "y", "duration_ms",
            "require_foreground_match", "window_title_regex", "window_handle", "window_pid",
            "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "click": {
            "x", "y", "button", "clicks",
            "require_foreground_match", "window_title_regex", "window_handle", "window_pid",
            "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "mouse_down": {
            "x", "y", "button",
            "require_foreground_match", "window_title_regex", "window_handle", "window_pid",
            "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "mouse_up": {
            "x", "y", "button",
            "require_foreground_match", "window_title_regex", "window_handle", "window_pid",
            "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "drag_to": {
            "x", "y", "duration_ms", "button", "mouse_down_up",
            "require_foreground_match", "window_title_regex", "window_handle", "window_pid",
            "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "type": {
            "text", "interval_ms",
            "require_foreground_match", "window_title_regex", "window_handle", "window_pid",
            "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "hotkey": {
            "keys",
            "require_foreground_match", "window_title_regex", "window_handle", "window_pid",
            "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "key_press": {
            "key", "presses", "interval_ms",
            "require_foreground_match", "window_title_regex", "window_handle", "window_pid",
            "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "scroll": {"clicks", "x", "y", "direction"},
        "wait": {"time_ms"},
        "focus_window": {"title_regex", "window_handle", "window_pid", "timeout_ms"},
        "focus_guard": {
            "title_regex", "window_handle", "window_pid",
            "mismatch_mode", "retries", "retry_wait_ms", "resolve_timeout_ms",
        },
        "launch_app": {"command", "process_name_for_policy"},
        "close_app": {"pid", "name_filter"},
        "screenshot": {"path", "monitor_index", "region"},
        "find_template": {"template_path", "monitor_index", "region", "threshold", "max_results", "grayscale"},
        "click_template": {
            "template_path", "monitor_index", "threshold", "button", "clicks",
            "max_attempts", "wait_between_attempts_ms",
        },
        "click_element": {
            "window_title_regex", "window_handle", "window_pid",
            "title", "auto_id", "control_type", "found_index", "timeout_ms",
            "template_path", "text_query", "button", "clicks", "threshold", "monitor_index", "region",
        },
        "element_click": {
            "window_title_regex", "window_handle", "window_pid",
            "title", "auto_id", "control_type", "found_index", "timeout_ms",
            "template_path", "text_query", "button", "clicks", "threshold", "monitor_index", "region",
        },
        "type_element": {
            "window_title_regex", "window_handle", "window_pid",
            "title", "auto_id", "control_type", "text", "found_index", "timeout_ms",
            "clear_first", "interval_ms", "template_path", "text_query", "threshold", "monitor_index", "region",
        },
        "element_type": {
            "window_title_regex", "window_handle", "window_pid",
            "title", "auto_id", "control_type", "text", "found_index", "timeout_ms",
            "clear_first", "interval_ms", "template_path", "text_query", "threshold", "monitor_index", "region",
        },
        "element_action": {
            "element_action",
            "window_title_regex", "window_handle", "window_pid",
            "title", "auto_id", "control_type", "found_index", "timeout_ms",
            "text", "clear_first", "allow_fallback",
            "template_path", "text_query", "monitor_index", "region", "threshold",
            "button", "clicks", "interval_ms",
            "require_foreground_match", "foreground_mismatch_mode", "foreground_retries", "foreground_retry_wait_ms",
        },
        "wait_for": {
            "condition",
            "window_title_regex", "window_handle", "window_pid",
            "title", "auto_id", "control_type", "found_index", "text_query", "timeout_ms", "poll_ms",
        },
        "assert": {
            "condition",
            "window_title_regex", "title", "auto_id", "control_type", "found_index", "text_query",
        },
        "transaction_start": {"transaction_name", "name_tag", "name", "mode"},
        "transaction_checkpoint": {"label", "monitor_index", "region"},
        "transaction_rollback_hint": {"reason", "max_hints"},
        "transaction_end": {"committed", "summary"},
        "transaction_status": set(),
        "read_text": {"image_path", "backend", "lang"},
        "assert_text": {"image_path", "contains"},
    }
    keys = action_fields.get(action, set(step.keys()))
    for key in sorted(keys):
        if key in step:
            normalized[key] = step[key]

    return normalized


def normalize_plan(plan: list[dict[str, Any]], drop_names: bool = False) -> dict[str, Any]:
    normalized_steps: list[dict[str, Any]] = []
    fingerprints: list[str] = []
    for idx, raw in enumerate(plan):
        if not isinstance(raw, dict):
            continue
        step = _normalize_step(raw)
        if drop_names:
            step.pop("name", None)
        step["_idx"] = idx
        blob = _stable_json(step)
        fp = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
        step["_fingerprint"] = fp
        fingerprints.append(fp)
        normalized_steps.append(step)
    plan_hash = hashlib.sha256(("|".join(fingerprints)).encode("utf-8")).hexdigest()
    return {"plan": normalized_steps, "count": len(normalized_steps), "hash": plan_hash}


def diff_plans(
    base_plan: list[dict[str, Any]],
    target_plan: list[dict[str, Any]],
    drop_names: bool = False,
) -> dict[str, Any]:
    left = normalize_plan(base_plan, drop_names=drop_names)
    right = normalize_plan(target_plan, drop_names=drop_names)
    lsteps = left["plan"]
    rsteps = right["plan"]

    max_len = max(len(lsteps), len(rsteps))
    changes: list[dict[str, Any]] = []
    same = 0
    for i in range(max_len):
        l = lsteps[i] if i < len(lsteps) else None
        r = rsteps[i] if i < len(rsteps) else None
        if l is None and r is not None:
            changes.append({"index": i, "type": "added", "target": r})
            continue
        if l is not None and r is None:
            changes.append({"index": i, "type": "removed", "base": l})
            continue
        if l is not None and r is not None:
            if l.get("_fingerprint") == r.get("_fingerprint"):
                same += 1
            else:
                changes.append({"index": i, "type": "changed", "base": l, "target": r})

    return {
        "same_steps": same,
        "changed_steps": len(changes),
        "base_count": len(lsteps),
        "target_count": len(rsteps),
        "base_hash": left["hash"],
        "target_hash": right["hash"],
        "changes": changes,
    }


def register_recording_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Recording, replay, and plan management. "
            "action: start (begin recording, name=, clear=true), "
            "stop (stop recording), status (recording info), "
            "get (retrieve events, limit=), clear (erase events), "
            "save (save to JSON, path=), load (load from JSON, path=, merge=false), "
            "replay (execute recorded events, stop_on_error=, delay_between_steps_ms=), "
            "to_plan (convert events to plan steps, normalize=true), "
            "plan_normalize (normalize a plan, plan=, drop_names=false), "
            "plan_diff (compare two plans, base_plan=, target_plan=), "
            "plan_load (load plan from file, path=), "
            "plan_save (save plan to file, plan=, path=, normalize=true), "
            "plan_compile (compile intents to plan, intents=, profile=balanced)."
        ),
    )
    async def desktop_recording(
        action: str,
        name: str | None = None,
        clear: bool = True,
        limit: int | None = None,
        path: str | None = None,
        merge: bool = False,
        stop_on_error: bool = True,
        delay_between_steps_ms: int = 0,
        default_ocr_backend: str = "auto",
        normalize: bool = True,
        drop_names: bool = False,
        plan: list[dict[str, Any]] | None = None,
        base_plan: list[dict[str, Any]] | None = None,
        target_plan: list[dict[str, Any]] | None = None,
        intents: list[dict[str, Any]] | None = None,
        profile: str = "balanced",
        wrap_in_transaction: bool = False,
        transaction_name: str | None = None,
        transaction_start_mode: str = "fail",
        auto_checkpoints: str = "major",
        include_metadata: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        act = action.strip().lower()

        if act == "start":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                st = STATE.recording_start(name=name, clear=clear, session_id=resolved_session_id)
                return ok(
                    "desktop_recording",
                    data={
                        "recording_active": st.recording_active,
                        "recording_started_at": st.recording_started_at,
                        "recording_name": st.recording_name,
                        "events": len(st.recording_events),
                    },
                    started_ms=started,
                )
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "stop":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                st = STATE.recording_stop(session_id=resolved_session_id)
                return ok(
                    "desktop_recording",
                    data={
                        "recording_active": st.recording_active,
                        "events": len(st.recording_events),
                        "recording_name": st.recording_name,
                    },
                    started_ms=started,
                )
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "status":
            resolved_session_id = STATE.resolve_session_id(session_id)
            st = STATE.get(session_id=resolved_session_id)
            return ok(
                "desktop_recording",
                data={
                    "recording_active": st.recording_active,
                    "recording_started_at": st.recording_started_at,
                    "recording_name": st.recording_name,
                    "events": len(st.recording_events),
                },
                started_ms=started,
            )

        elif act == "get":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                events = STATE.recording_get_events(session_id=resolved_session_id)
                if limit is not None and limit > 0:
                    events = events[-int(limit):]
                elif limit is not None and limit == 0:
                    events = []
                return ok("desktop_recording", data={"events": events, "count": len(events)}, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "clear":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                st = STATE.recording_clear(session_id=resolved_session_id)
                return ok("desktop_recording", data={"events": len(st.recording_events)}, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "save":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                events = STATE.recording_get_events(session_id=resolved_session_id)
                out_path = path
                if out_path is None:
                    out_dir = artifacts_subdir("recordings")
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                    out_path = str(out_dir / f"recording_{ts}.json")
                payload = {
                    "name": STATE.get(session_id=resolved_session_id).recording_name,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "events": events,
                }
                Path(out_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return ok("desktop_recording", data={"path": out_path, "count": len(events)}, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "load":
            try:
                if not path:
                    return err("desktop_recording", "path is required for load", started_ms=started, code="validation_error")
                raw = Path(path).read_text(encoding="utf-8")
                payload = json.loads(raw)
                events = payload.get("events")
                if not isinstance(events, list):
                    raise RuntimeError("Invalid recording file: missing events[]")
                resolved_session_id = STATE.resolve_session_id(session_id)
                st = STATE.recording_set_events(events=events, merge=merge, session_id=resolved_session_id)
                return ok(
                    "desktop_recording",
                    data={"events": len(st.recording_events), "merge": merge, "name": payload.get("name")},
                    started_ms=started,
                )
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "replay":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                events = STATE.recording_get_events(session_id=resolved_session_id)
                recorded_plan = _to_plan(events)
                if normalize:
                    recorded_plan = normalize_plan(recorded_plan, drop_names=drop_names)["plan"]
                if delay_between_steps_ms > 0:
                    for step in recorded_plan:
                        step.setdefault("wait_after_ms", delay_between_steps_ms)
                result = await execute_plan(
                    plan=recorded_plan,
                    settings=settings,
                    stop_on_error=stop_on_error,
                    default_ocr_backend=default_ocr_backend,
                    session_id=resolved_session_id,
                    ctx=ctx,
                )
                result["action"] = "desktop_recording"
                result["data"] = result.get("data", {})
                result["data"]["source_events"] = len(events)
                result["data"]["replay_steps"] = len(recorded_plan)
                result["data"]["normalized"] = normalize
                result["timing_ms"] = now_ms() - started
                return result
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "to_plan":
            try:
                resolved_session_id = STATE.resolve_session_id(session_id)
                events = STATE.recording_get_events(session_id=resolved_session_id)
                recorded_plan = _to_plan(events)
                if normalize:
                    data = normalize_plan(recorded_plan, drop_names=drop_names)
                    data["normalized"] = True
                else:
                    data = {"plan": recorded_plan, "count": len(recorded_plan), "normalized": False}
                data["source_events"] = len(events)
                return ok("desktop_recording", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "plan_normalize":
            try:
                data = normalize_plan(plan or [], drop_names=drop_names)
                return ok("desktop_recording", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "plan_diff":
            try:
                data = diff_plans(base_plan=base_plan or [], target_plan=target_plan or [], drop_names=drop_names)
                return ok("desktop_recording", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "plan_load":
            try:
                if not path:
                    return err("desktop_recording", "path is required for plan_load", started_ms=started, code="validation_error")
                raw = Path(path).read_text(encoding="utf-8")
                payload = json.loads(raw)
                if isinstance(payload, dict) and isinstance(payload.get("plan"), list):
                    loaded_plan = payload["plan"]
                elif isinstance(payload, list):
                    loaded_plan = payload
                else:
                    raise RuntimeError("Invalid plan file. Expected {'plan': [...]} or [...]")
                return ok("desktop_recording", data={"plan": loaded_plan, "count": len(loaded_plan)}, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "plan_save":
            try:
                save_plan = plan or []
                out = normalize_plan(save_plan, drop_names=drop_names) if normalize else {"plan": save_plan, "count": len(save_plan), "hash": ""}
                out_path = path
                if out_path is None:
                    out_dir = artifacts_subdir("plans")
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                    out_path = str(out_dir / f"plan_{ts}.json")
                payload = {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "normalized": normalize,
                    "count": out["count"],
                    "hash": out.get("hash", ""),
                    "plan": out["plan"],
                }
                Path(out_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return ok("desktop_recording", data={"path": out_path, "count": out["count"], "hash": out.get("hash", "")}, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        elif act == "plan_compile":
            try:
                from novaforge.tools.compiler_tools import compile_intents
                data = compile_intents(
                    intents=intents or [],
                    profile=profile,
                    stop_on_error=stop_on_error,
                    default_ocr_backend=default_ocr_backend,
                    wrap_in_transaction=wrap_in_transaction,
                    transaction_name=transaction_name,
                    transaction_start_mode=transaction_start_mode,
                    auto_checkpoints=auto_checkpoints,
                    include_metadata=include_metadata,
                )
                if data.get("ok") is False:
                    return data
                return ok("desktop_recording", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_recording", str(e), started_ms=started)

        else:
            return err(
                "desktop_recording",
                f"Unknown action: {action}",
                hint="Use one of: start, stop, status, get, clear, save, load, replay, to_plan, plan_normalize, plan_diff, plan_load, plan_save, plan_compile",
                started_ms=started,
                code="validation_error",
            )
