from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastmcp import Context
from fastmcp import FastMCP
from fastmcp.utilities.types import Image as MCPImage
from mcp.types import SamplingMessage, TextContent

from novaforge.config import Settings, load_settings
from novaforge.elevated_broker import ELEVATED_BROKER
from novaforge.execution_runtime import RuntimeController, policy_from_options
from novaforge.failure_envelope import analyze_failure_envelope
from novaforge.engines.input_engine import INPUT_ENGINE
from novaforge.engines.ocr_engine import OCR_ENGINE
from novaforge.engines.artifact_manager import ARTIFACT_MANAGER
from novaforge.engines.screen_engine import SCREEN_ENGINE
from novaforge.engines.transaction_engine import TRANSACTION_ENGINE
from novaforge.engines.vision_pipeline import VisionPipeline
from novaforge.engines.window_engine import WINDOW_ENGINE
from novaforge.errors import error_code_for_exception
from novaforge.errors import ArtifactPathError
from novaforge.result import err, now_ms, ok
from novaforge.safety import assert_can_run, assert_process_allowed, process_name_from_command
from novaforge.schemas import validate_plan_with_errors
from novaforge.state import STATE

SUPPORTED_ACTIONS = {
    "wait",
    "move_mouse",
    "click",
    "mouse_down",
    "mouse_up",
    "drag_to",
    "scroll",
    "type",
    "hotkey",
    "key_press",
    "focus_window",
    "focus_guard",
    "list_windows",
    "launch_app",
    "close_app",
    "screenshot",
    "find_template",
    "click_template",
    "click_element",
    "element_click",
    "type_element",
    "element_type",
    "element_action",
    "wait_for",
    "assert",
    "read_text",
    "assert_text",
    "transaction_start",
    "transaction_checkpoint",
    "transaction_rollback_hint",
    "transaction_end",
    "transaction_status",
    "if",
    "repeat",
    "while",
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    try:
        out = int(value)
    except Exception as e:
        raise RuntimeError(f"Invalid {field_name}: {value}") from e
    if out <= 0:
        raise RuntimeError(f"Invalid {field_name}: {value}. Must be > 0")
    return out


def _effective_target(step: dict[str, Any], session_id: str | None = None) -> tuple[str | None, int | None, int | None]:
    title_regex = step.get("window_title_regex")
    window_handle = step.get("window_handle")
    window_pid = step.get("window_pid")
    explicit = (
        window_handle is not None
        or window_pid is not None
        or bool(str(title_regex or "").strip())
    )
    if explicit:
        return (
            str(title_regex) if title_regex is not None else None,
            _optional_positive_int(window_handle, "window_handle"),
            _optional_positive_int(window_pid, "window_pid"),
        )
    st = STATE.get(session_id=session_id)
    return st.bound_window_title_regex, st.bound_window_handle, st.bound_window_pid


def _is_mutating_action(action: str) -> bool:
    return action in {
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
    }


def _run_canary_checks(
    *,
    action: str,
    step: dict[str, Any],
    settings: Settings,
    session_id: str | None = None,
) -> dict[str, Any]:
    checks: dict[str, Any] = {"enabled": True, "action": action, "checks": []}
    if action == "screenshot":
        out_path = step.get("path")
        if out_path:
            ARTIFACT_MANAGER.ensure_parent_dir(str(out_path))
            checks["checks"].append({"name": "artifact_parent_ready", "ok": True, "path": str(out_path)})
    if action in {"read_text", "assert_text"}:
        image_path = str(step.get("image_path", ""))
        if not image_path or not Path(image_path).exists():
            if action == "read_text" and settings.best_effort_ocr:
                checks["checks"].append(
                    {
                        "name": "ocr_image_exists",
                        "ok": False,
                        "warning": "missing_image_best_effort",
                        "path": image_path,
                    }
                )
            else:
                raise ArtifactPathError(
                    f"Image not found: {image_path}",
                    details={"image_path": image_path, "stage": "canary_precheck"},
                )
        else:
            checks["checks"].append({"name": "ocr_image_exists", "ok": True, "path": image_path})
    if action in {"focus_window", "focus_guard", "click_element", "element_click", "type_element", "element_type", "element_action", "wait_for", "assert"}:
        target_regex, target_handle, target_pid = _effective_target(step, session_id=session_id)
        if target_handle is not None or target_pid is not None or (target_regex or "").strip():
            window = WINDOW_ENGINE.resolve_window(
                title_regex=target_regex,
                window_handle=target_handle,
                window_pid=target_pid,
                timeout_ms=min(2000, _as_int(step.get("timeout_ms"), 2000)),
            )
            checks["checks"].append(
                {
                    "name": "target_window_resolves",
                    "ok": True,
                    "window_handle": int(getattr(window, "handle", 0)) or None,
                }
            )
    if _is_mutating_action(action):
        fg = WINDOW_ENGINE.get_foreground_window()
        checks["checks"].append({"name": "foreground_present", "ok": bool(fg.get("handle")), "foreground": fg})
    return checks


def _apply_auto_fallback_policy(
    *,
    action: str,
    step: dict[str, Any],
    default_ocr_backend: str,
    fallback_policy: str,
) -> tuple[dict[str, Any], list[str]]:
    out = dict(step)
    notes: list[str] = []
    profile = str(fallback_policy or "conservative").strip().lower()
    if profile not in {"conservative", "aggressive"}:
        return out, notes

    if action in {"click_element", "element_click", "type_element", "element_type", "element_action"}:
        if not _coerce_bool(out.get("allow_fallback", True)):
            out["allow_fallback"] = True
            notes.append("enabled_element_fallback")
        if profile == "aggressive" and not out.get("fallback_chain"):
            out["fallback_chain"] = ["uia", "template", "ocr_text"]
            notes.append("added_aggressive_fallback_chain")

    if action == "read_text":
        backend = str(out.get("backend", default_ocr_backend)).strip().lower()
        if backend in {"local_model", "tesseract", "rapidocr"}:
            out["backend"] = "auto"
            notes.append(f"backend_{backend}_promoted_to_auto")
        out["stabilization_attempts"] = max(3, _as_int(out.get("stabilization_attempts"), 3))
        out["min_consensus"] = max(2, _as_int(out.get("min_consensus"), 2))
        if profile == "aggressive":
            out["stabilization_attempts"] = max(5, _as_int(out.get("stabilization_attempts"), 5))
            notes.append("aggressive_ocr_stabilization")

    if action == "click_template":
        out["max_attempts"] = max(3, _as_int(out.get("max_attempts"), 3))
        if profile == "aggressive":
            out["max_attempts"] = max(5, _as_int(out.get("max_attempts"), 5))
            threshold = float(out.get("threshold", 0.85))
            out["threshold"] = max(0.70, min(0.95, threshold - 0.05))
            notes.append("aggressive_template_threshold_relaxation")

    return out, notes


def _foreground_policy(step: dict[str, Any], action: str) -> dict[str, Any]:
    default_required = action in {
        "click_template",
        "click_element",
        "element_click",
        "type_element",
        "element_type",
        "element_action",
    }
    return {
        "required": _coerce_bool(step.get("require_foreground_match", default_required)),
        "mode": str(step.get("foreground_mismatch_mode", "refocus_and_retry")),
        "retries": max(1, _as_int(step.get("foreground_retries"), 3)),
        "retry_wait_ms": max(10, _as_int(step.get("foreground_retry_wait_ms"), 150)),
    }


def _guard_foreground_for_step(
    step: dict[str, Any],
    action: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    policy = _foreground_policy(step, action)
    if not policy["required"]:
        return None
    target_regex, target_handle, target_pid = _effective_target(step, session_id=session_id)
    if target_handle is None and target_pid is None and not (target_regex or "").strip():
        raise RuntimeError(
            "Foreground guard requires a window target. Provide window_handle/window_pid/window_title_regex or bind a window."
        )
    return WINDOW_ENGINE.ensure_foreground_target(
        expected_handle=target_handle,
        expected_pid=target_pid,
        expected_title_regex=target_regex,
        mismatch_mode=policy["mode"],
        retries=policy["retries"],
        retry_wait_ms=policy["retry_wait_ms"],
    )


def _step_error(step: dict[str, Any], e: Exception, started: int) -> dict[str, Any]:
    return err(
        "desktop_act",
        f"Step '{step.get('name', step.get('action', 'unknown'))}' failed: {e}",
        started_ms=started,
    )


def _classify_error(e: Exception) -> str:
    typed = error_code_for_exception(e, default="")
    if typed:
        return typed
    msg = str(e).lower()
    if "invalid window_handle" in msg or "invalid window_pid" in msg:
        return "validation_error"
    if "requires non-empty text_query" in msg:
        return "validation_error"
    if "conflicting routing directives" in msg:
        return "validation_error"
    if "privilege mismatch" in msg or "administrator" in msg:
        return "permission_denied"
    if "foreground mismatch" in msg:
        return "foreground_mismatch"
    if "foreground refocus failed" in msg:
        return "foreground_refocus_failed"
    if "foreground guard requires a window target" in msg:
        return "foreground_unresolvable_target"
    if "allowlist" in msg or "blocked by allowlist policy" in msg:
        return "policy_blocked"
    if "session is not active" in msg:
        return "session_inactive"
    if "max action limit reached" in msg:
        return "action_limit_reached"
    if "timed out" in msg:
        return "timeout"
    if "not found" in msg or "no window matched" in msg:
        return "not_found"
    if "unsupported" in msg:
        return "unsupported_action"
    if "failsafe" in msg:
        return "failsafe_triggered"
    if "broker" in msg:
        return "broker_unavailable"
    return "runtime_error"


def _is_canonical_step(step: dict[str, Any]) -> bool:
    required = {"name", "action", "attempt", "ok", "data", "error", "observation", "confirmation", "runtime_meta"}
    return required.issubset(set(step.keys()))


def _transaction_failure_recovery(
    reason: str,
    generate_rollback_hint: bool,
    end_transaction: bool,
    max_hints: int,
    session_id: str | None = None,
) -> dict[str, Any]:
    recovery: dict[str, Any] = {"transaction_active": False}
    try:
        status = TRANSACTION_ENGINE.status(session_id=session_id)
    except Exception as e:
        return {"transaction_active": False, "status_error": str(e)}

    if not bool(status.get("transaction_active")):
        return recovery

    recovery["transaction_active"] = True
    recovery["transaction_id"] = status.get("transaction_id")
    if generate_rollback_hint:
        try:
            recovery["rollback_hint"] = TRANSACTION_ENGINE.rollback_hint(
                reason=reason,
                max_hints=max_hints,
                session_id=session_id,
            )
        except Exception as e:
            recovery["rollback_hint_error"] = str(e)
    if end_transaction:
        try:
            recovery["transaction_end"] = TRANSACTION_ENGINE.end(
                committed=False,
                summary=reason,
                session_id=session_id,
            )
        except Exception as e:
            recovery["transaction_end_error"] = str(e)
    return recovery


async def _read_text_host_model(
    *,
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


def _click_by_template(step: dict[str, Any]) -> dict[str, Any]:
    template_path = str(step.get("template_path", "")).strip()
    if not template_path:
        raise RuntimeError("Missing template_path for template fallback")
    found = SCREEN_ENGINE.find_template(
        template_path=template_path,
        monitor_index=_as_int(step.get("monitor_index"), 1),
        threshold=float(step.get("threshold", 0.85)),
        max_results=1,
        grayscale=True,
    )
    if found["count"] < 1 or not found.get("best"):
        return {"strategy": "template", "matched": False, "warning": "Template fallback could not find match", "scan": found}
    best = found["best"]
    click = INPUT_ENGINE.click(
        x=int(best["center_x"]),
        y=int(best["center_y"]),
        button=str(step.get("button", "left")),
        clicks=_as_int(step.get("clicks"), 1),
    )
    return {"strategy": "template", "match": best, "click": click}


def _click_by_ocr_text(step: dict[str, Any]) -> dict[str, Any]:
    text_query = str(step.get("text_query", "")).strip()
    if not text_query:
        raise RuntimeError("Missing text_query for OCR fallback")
    monitor_index = _as_int(step.get("monitor_index"), 1)
    region = step.get("region")
    region_dict = region if isinstance(region, dict) else None
    shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index, region=region_dict)
    near_x = None
    near_y = None
    if region_dict:
        near_x = int(region_dict["width"] / 2)
        near_y = int(region_dict["height"] / 2)
    ranked = OCR_ENGINE.select_text_target_local_model(
        image_path=str(shot["path"]),
        query=text_query,
        near_x=near_x,
        near_y=near_y,
    )
    if ranked["count"] < 1 or not ranked.get("best"):
        raise RuntimeError(f"OCR fallback could not find text: {text_query}")
    best = ranked["best"]
    offset_left = int(region_dict["left"]) if region_dict and "left" in region_dict else 0
    offset_top = int(region_dict["top"]) if region_dict and "top" in region_dict else 0
    click = INPUT_ENGINE.click(
        x=int(best["center_x"] + offset_left),
        y=int(best["center_y"] + offset_top),
        button=str(step.get("button", "left")),
        clicks=_as_int(step.get("clicks"), 1),
    )
    return {"strategy": "ocr_text", "match": best, "ranked": ranked["matches"], "click": click, "screenshot": shot}


def _click_element_with_fallback(step: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    failures: list[str] = []
    target_regex, target_handle, target_pid = _effective_target(step, session_id=session_id)

    try:
        uia = WINDOW_ENGINE.click_element(
            window_title_regex=target_regex,
            title=step.get("title"),
            auto_id=step.get("auto_id"),
            control_type=step.get("control_type"),
            found_index=_as_int(step.get("found_index"), 0),
            window_handle=target_handle,
            window_pid=target_pid,
            timeout_ms=_as_int(step.get("timeout_ms"), 4000),
        )
        if uia.get("strategy") == "uia_center_fallback":
            rect = uia["rect"]
            click = INPUT_ENGINE.click(
                x=int(rect["center_x"]),
                y=int(rect["center_y"]),
                button=str(step.get("button", "left")),
                clicks=_as_int(step.get("clicks"), 1),
            )
            return {"strategy": "uia_center_click", "uia": uia, "click": click}
        return {"strategy": "uia", "uia": uia}
    except Exception as e:
        failures.append(f"uia failed: {e}")

    if step.get("template_path"):
        try:
            data = _click_by_template(step)
            data["fallback_notes"] = failures
            return data
        except Exception as e:
            failures.append(f"template fallback failed: {e}")

    if step.get("text_query"):
        data = _click_by_ocr_text(step)
        data["fallback_notes"] = failures
        return data

    raise RuntimeError("; ".join(failures) if failures else "No fallback available")


def _check_element_condition(
    step: dict[str, Any],
    condition: str,
    session_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    target_regex, target_handle, target_pid = _effective_target(step, session_id=session_id)
    cond = condition.strip().lower()
    if cond == "gone":
        try:
            snap = WINDOW_ENGINE.snapshot_element(
                window_title_regex=target_regex,
                window_handle=target_handle,
                window_pid=target_pid,
                title=step.get("title"),
                auto_id=step.get("auto_id"),
                control_type=step.get("control_type"),
                found_index=_as_int(step.get("found_index"), 0),
                timeout_ms=_as_int(step.get("timeout_ms"), 4000),
            )
            return False, {"exists": True, "snapshot": snap}
        except Exception:
            return True, {"exists": False}

    snap = WINDOW_ENGINE.snapshot_element(
        window_title_regex=target_regex,
        window_handle=target_handle,
        window_pid=target_pid,
        title=step.get("title"),
        auto_id=step.get("auto_id"),
        control_type=step.get("control_type"),
        found_index=_as_int(step.get("found_index"), 0),
        timeout_ms=_as_int(step.get("timeout_ms"), 4000),
    )
    if cond == "exists":
        return True, {"snapshot": snap}
    if cond == "visible":
        return bool(snap.get("visible")), {"snapshot": snap}
    if cond == "enabled":
        return bool(snap.get("enabled")), {"snapshot": snap}
    if cond == "focused":
        return bool(snap.get("focused")), {"snapshot": snap}
    if cond == "text_contains":
        q = str(step.get("text_query") or "").strip().lower()
        if not q:
            raise RuntimeError("text_contains requires non-empty text_query")
        candidates = [str(x) for x in (snap.get("text_candidates") or [])]
        if not candidates:
            candidates = [str(snap.get("title", ""))]
        matched = any(q in c.lower() for c in candidates)
        return matched, {"query": q, "candidates": candidates, "snapshot": snap}
    raise RuntimeError(f"Unsupported condition: {condition}")


_MAX_RECURSION_DEPTH = 10


def _evaluate_flow_condition(
    step: dict[str, Any],
    settings: Settings,
    session_id: str | None = None,
) -> bool:
    import re as _re  # local import to avoid circular dependency

    condition = str(step.get("condition", "")).strip().lower()

    if condition == "text_visible":
        text = str(step.get("text", "")).strip()
        if not text:
            raise RuntimeError("text_visible condition requires 'text'")
        monitor_index = _as_int(step.get("monitor_index"), 1)
        shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index)
        ranked = OCR_ENGINE.select_text_target_local_model(
            image_path=str(shot["path"]),
            query=text,
        )
        return ranked.get("count", 0) > 0

    elif condition == "text_gone":
        text = str(step.get("text", "")).strip()
        if not text:
            raise RuntimeError("text_gone condition requires 'text'")
        monitor_index = _as_int(step.get("monitor_index"), 1)
        shot = SCREEN_ENGINE.capture(path=None, monitor_index=monitor_index)
        ranked = OCR_ENGINE.select_text_target_local_model(
            image_path=str(shot["path"]),
            query=text,
        )
        return ranked.get("count", 0) == 0

    elif condition == "window_exists":
        title_regex = step.get("title_regex") or step.get("window_title_regex", "")
        if not title_regex:
            raise RuntimeError("window_exists condition requires 'title_regex'")
        try:
            _re.compile(str(title_regex))
        except _re.error as e:
            raise RuntimeError(f"Invalid regex: {e}") from e
        wins = WINDOW_ENGINE.list_windows(only_visible=True)
        return any(_re.search(str(title_regex), w.get("title", ""), _re.IGNORECASE) for w in wins)

    elif condition in {"element_exists", "exists"}:
        passed, _ = _check_element_condition(step, "exists", session_id=session_id)
        return passed

    elif condition in {"element_gone", "gone"}:
        passed, _ = _check_element_condition(step, "gone", session_id=session_id)
        return passed

    else:
        raise RuntimeError(f"Unsupported flow condition: {condition}")


async def execute_plan(
    plan: list[dict[str, Any]],
    settings: Settings,
    stop_on_error: bool = True,
    default_ocr_backend: str = "auto",
    on_error_generate_rollback_hint: bool = True,
    on_error_end_transaction: bool = True,
    on_error_max_hints: int = 5,
    runtime_profile: str = "basic_reliable",
    runtime_options: dict[str, Any] | None = None,
    enforce_preflight: bool = False,
    session_id: str | None = None,
    ctx: Context | None = None,
    _depth: int = 0,
) -> dict[str, Any]:
    started = now_ms()
    if _depth > _MAX_RECURSION_DEPTH:
        return err("desktop_act", f"Max nesting depth exceeded ({_MAX_RECURSION_DEPTH})", started_ms=started, code="validation_error")
    resolved_session_id = STATE.resolve_session_id(session_id)
    runtime = RuntimeController(
        policy_from_options(profile=runtime_profile, options=runtime_options),
        session_id=resolved_session_id,
    )
    pipeline = VisionPipeline(OCR_ENGINE)
    try:
        assert_can_run(settings, session_id=resolved_session_id)
        if not plan:
            return err("desktop_act", "Plan is empty", started_ms=started, code="validation_error")

        validated, validation_errors = validate_plan_with_errors(plan=plan)
        if validated is None:
            if runtime.policy.allow_unknown_actions:
                # Degrade gracefully only for unknown actions; keep strict schema validation for known actions.
                normalized: list[dict[str, Any]] = []
                known_action_errors: list[dict[str, Any]] = []
                for idx_raw, step_raw in enumerate(plan):
                    if not isinstance(step_raw, dict):
                        known_action_errors.append(
                            {
                                "index": idx_raw,
                                "action": "unknown",
                                "errors": [{"type": "type_error", "msg": "Step must be an object"}],
                            }
                        )
                        continue
                    raw_action = str(step_raw.get("action") or "").strip().lower()
                    if raw_action in SUPPORTED_ACTIONS:
                        one_validated, one_errors = validate_plan_with_errors(plan=[step_raw])
                        if one_validated is None:
                            known_action_errors.append(
                                {
                                    "index": idx_raw,
                                    "action": raw_action,
                                    "errors": one_errors,
                                }
                            )
                            continue
                        normalized.append(one_validated[0])
                    else:
                        normalized.append(step_raw)
                if known_action_errors:
                    return err(
                        "desktop_act",
                        "Plan validation failed for known actions",
                        started_ms=started,
                        code="validation_error",
                        details={"errors": known_action_errors},
                    )
                validated = normalized
            else:
                return err(
                    "desktop_act",
                    "Plan validation failed",
                    started_ms=started,
                    code="validation_error",
                    details={"errors": validation_errors},
                )
        if validated is None:
            return err(
                "desktop_act",
                "Plan validation failed",
                started_ms=started,
                code="validation_error",
                details={"errors": validation_errors},
            )
        plan = validated
        preflight = analyze_failure_envelope(
            plan=plan,
            settings=settings,
            runtime_profile=runtime_profile,
            runtime_options=runtime_options,
        )
        if enforce_preflight and not bool(preflight.get("ready", False)):
            return err(
                "desktop_act",
                "Preflight failure envelope reported hard blockers",
                started_ms=started,
                code="preflight_blocked",
                details={"preflight": preflight},
            )

        steps: list[dict[str, Any]] = []
        for idx, step in enumerate(plan):
            runtime.check_budget(idx)
            name = str(step.get("name") or f"step_{idx + 1}")
            action = str(step.get("action") or "").strip().lower()
            retries = max(1, _as_int(step.get("retries"), runtime.policy.default_retries))
            wait_after_ms = max(0, _as_int(step.get("wait_after_ms"), 0))
            runtime.ensure_allowed_action(action=action, confirmed=_coerce_bool(step.get("confirm", False)))

            if not action:
                failure = err("desktop_act", f"Step '{name}' missing 'action'", started_ms=started)
                if stop_on_error:
                    recovery = _transaction_failure_recovery(
                        reason=f"Step '{name}' missing action",
                        generate_rollback_hint=on_error_generate_rollback_hint,
                        end_transaction=on_error_end_transaction,
                        max_hints=on_error_max_hints,
                        session_id=resolved_session_id,
                    )
                    failure["data"] = {"transaction_recovery": recovery}
                    failure["error"]["code"] = "validation_error"
                    return failure
                step_error = {
                    "code": "validation_error",
                    "message": f"Step '{name}' missing 'action'",
                    "hint": runtime.error_hint("validation_error"),
                    "details": {},
                }
                failed_step = runtime.canonical_step(
                    name=name,
                    action="unknown",
                    attempt=1,
                    ok=False,
                    error=step_error,
                    strategy_path=["validation"],
                )
                steps.append(failed_step)
                runtime.append_transcript(name=name, action="unknown", params=step, result=failed_step)
                continue

            final_result: dict[str, Any] | None = None
            last_error: Exception | None = None
            step, fallback_policy_notes = _apply_auto_fallback_policy(
                action=action,
                step=step,
                default_ocr_backend=default_ocr_backend,
                fallback_policy=runtime.policy.fallback_policy if runtime.policy.auto_fallback else "none",
            )
            canary_report: dict[str, Any] | None = None

            for attempt in range(1, retries + 1):
                try:
                    assert_can_run(settings, session_id=resolved_session_id)
                    if runtime.policy.canary_checks:
                        canary_report = _run_canary_checks(
                            action=action,
                            step=step,
                            settings=settings,
                            session_id=resolved_session_id,
                        )
                    STATE.mark_action(session_id=resolved_session_id)
                    data: dict[str, Any]
                    pre_guard: dict[str, Any] | None = None
                    post_guard: dict[str, Any] | None = None
                    pre_observation: dict[str, Any] | None = None
                    post_observation: dict[str, Any] | None = None
                    broker_route_note: dict[str, Any] | None = None
                    if runtime.should_observe(action):
                        try:
                            pre_observation = {
                                "foreground": WINDOW_ENGINE.get_foreground_window(),
                                "state_handoff": runtime.state_handoff(),
                            }
                        except Exception as obs_e:
                            pre_observation = {"warning": str(obs_e)}

                    route_broker, route_reason = ELEVATED_BROKER.should_route_step(
                        settings=settings,
                        step=step,
                        action=action,
                        runtime_options=runtime_options,
                    )
                    if route_reason == "conflicting_route_directives":
                        raise RuntimeError("Conflicting routing directives: force_local and force_broker cannot both be true")
                    if route_broker:
                        broker_fallback_local = _coerce_bool((runtime_options or {}).get("broker_fallback_local", False))
                        try:
                            broker_data = ELEVATED_BROKER.execute_step(
                                settings=settings,
                                step=step,
                                runtime_profile=runtime.policy.profile,
                                runtime_options=runtime_options,
                                default_ocr_backend=default_ocr_backend,
                            )
                        except Exception as broker_e:
                            if not broker_fallback_local:
                                raise RuntimeError(f"Broker route failed ({route_reason}): {broker_e}") from broker_e
                            route_broker = False
                            route_reason = f"{route_reason}_fallback_local"
                            broker_route_note = {
                                "attempted": True,
                                "route_reason": route_reason,
                                "fallback_local": True,
                                "error": str(broker_e),
                            }
                        if not route_broker:
                            pass
                        else:
                            broker_step = broker_data.get("step")
                            if not isinstance(broker_step, dict):
                                if broker_fallback_local:
                                    broker_route_note = {
                                        "attempted": True,
                                        "route_reason": f"{route_reason}_fallback_local",
                                        "fallback_local": True,
                                        "error": "Broker returned non-object step",
                                    }
                                    route_broker = False
                                else:
                                    raise RuntimeError("Broker did not return canonical step")
                            if not route_broker:
                                pass
                            elif not _is_canonical_step(broker_step):
                                if broker_fallback_local:
                                    broker_route_note = {
                                        "attempted": True,
                                        "route_reason": f"{route_reason}_fallback_local",
                                        "fallback_local": True,
                                        "error": "Broker returned non-canonical step",
                                    }
                                    route_broker = False
                                else:
                                    raise RuntimeError("Broker returned non-canonical step")
                            elif not bool(broker_step.get("ok")):
                                if broker_fallback_local:
                                    broker_route_note = {
                                        "attempted": True,
                                        "route_reason": f"{route_reason}_fallback_local",
                                        "fallback_local": True,
                                        "error": f"Broker step reported ok=false: {broker_step.get('error')}",
                                    }
                                    route_broker = False
                                else:
                                    raise RuntimeError(f"Broker step reported failure: {broker_step.get('error')}")
                            if not route_broker:
                                pass
                            else:
                                broker_meta: dict[str, Any] = {
                                    "strategy": "broker",
                                    "route_reason": route_reason,
                                    "broker_status": ELEVATED_BROKER.status(settings=settings),
                                }
                                broker_step = dict(broker_step)
                                broker_step["name"] = name
                                broker_step["action"] = action
                                broker_step["attempt"] = attempt
                                step_data = broker_step.get("data")
                                if isinstance(step_data, dict):
                                    step_data["broker"] = broker_meta
                                else:
                                    broker_step["data"] = {"broker": broker_meta}
                                runtime_meta = broker_step.get("runtime_meta")
                                if isinstance(runtime_meta, dict):
                                    strategy = runtime_meta.get("strategy_path")
                                    if isinstance(strategy, list):
                                        if "broker" not in strategy:
                                            strategy.append("broker")
                                    else:
                                        runtime_meta["strategy_path"] = [action, "broker"]
                                    runtime_meta["profile"] = runtime.policy.profile
                                else:
                                    broker_step["runtime_meta"] = {
                                        "profile": runtime.policy.profile,
                                        "strategy_path": [action, "broker"],
                                    }
                                final_result = broker_step
                                runtime.append_transcript(name=name, action=action, params=step, result=final_result)
                                break
                    if _is_mutating_action(action):
                        pre_guard = _guard_foreground_for_step(
                            step=step,
                            action=action,
                            session_id=resolved_session_id,
                        )
                    if action == "wait":
                        data = INPUT_ENGINE.sleep(_as_int(step.get("time_ms"), 0))
                    elif action == "move_mouse":
                        data = INPUT_ENGINE.move(
                            x=_as_int(step.get("x")),
                            y=_as_int(step.get("y")),
                            duration_ms=_as_int(step.get("duration_ms"), 0),
                        )
                    elif action == "click":
                        data = INPUT_ENGINE.click(
                            x=_as_int(step.get("x")),
                            y=_as_int(step.get("y")),
                            button=str(step.get("button", "left")),
                            clicks=_as_int(step.get("clicks"), 1),
                        )
                    elif action == "mouse_down":
                        x = step.get("x")
                        y = step.get("y")
                        data = INPUT_ENGINE.mouse_down(
                            x=_as_int(x) if x is not None else None,
                            y=_as_int(y) if y is not None else None,
                            button=str(step.get("button", "left")),
                        )
                    elif action == "mouse_up":
                        x = step.get("x")
                        y = step.get("y")
                        data = INPUT_ENGINE.mouse_up(
                            x=_as_int(x) if x is not None else None,
                            y=_as_int(y) if y is not None else None,
                            button=str(step.get("button", "left")),
                        )
                    elif action == "drag_to":
                        data = INPUT_ENGINE.drag_to(
                            x=_as_int(step.get("x")),
                            y=_as_int(step.get("y")),
                            duration_ms=_as_int(step.get("duration_ms"), 200),
                            button=str(step.get("button", "left")),
                            mouse_down_up=_coerce_bool(step.get("mouse_down_up", True)),
                        )
                    elif action == "scroll":
                        scroll_x = step.get("x")
                        scroll_y = step.get("y")
                        direction = str(step.get("direction", "vertical")).strip().lower()
                        if direction == "horizontal":
                            data = INPUT_ENGINE.hscroll(
                                clicks=_as_int(step.get("clicks"), 0),
                                x=_as_int(scroll_x) if scroll_x is not None else None,
                                y=_as_int(scroll_y) if scroll_y is not None else None,
                            )
                        else:
                            data = INPUT_ENGINE.scroll(
                                clicks=_as_int(step.get("clicks"), 0),
                                x=_as_int(scroll_x) if scroll_x is not None else None,
                                y=_as_int(scroll_y) if scroll_y is not None else None,
                            )
                    elif action == "type":
                        data = INPUT_ENGINE.type_text(
                            text=str(step.get("text", "")),
                            interval_ms=_as_int(step.get("interval_ms"), 0),
                        )
                    elif action == "hotkey":
                        keys = step.get("keys") or []
                        if not isinstance(keys, list):
                            raise RuntimeError("keys must be a list")
                        data = INPUT_ENGINE.hotkey([str(k) for k in keys])
                    elif action == "key_press":
                        data = INPUT_ENGINE.key_press(
                            key=str(step.get("key")),
                            presses=_as_int(step.get("presses"), 1),
                            interval_ms=_as_int(step.get("interval_ms"), 0),
                        )
                    elif action == "focus_window":
                        target_regex, target_handle, target_pid = _effective_target(step, session_id=resolved_session_id)
                        if target_handle is not None:
                            data = WINDOW_ENGINE.focus_window_by_handle(
                                window_handle=int(target_handle),
                                timeout_ms=_as_int(step.get("timeout_ms"), 4000),
                            )
                        elif target_pid is not None:
                            data = WINDOW_ENGINE.focus_window_by_pid(
                                pid=int(target_pid),
                                timeout_ms=_as_int(step.get("timeout_ms"), 5000),
                            )
                        else:
                            data = WINDOW_ENGINE.focus_window(
                                title_regex=str(step.get("title_regex") or target_regex or ""),
                                timeout_ms=_as_int(step.get("timeout_ms"), 5000),
                            )
                        STATE.set_focus(
                            data.get("title"),
                            handle=data.get("handle"),
                            pid=data.get("pid"),
                            session_id=resolved_session_id,
                        )
                    elif action == "focus_guard":
                        target_regex, target_handle, target_pid = _effective_target(step, session_id=resolved_session_id)
                        data = WINDOW_ENGINE.ensure_foreground_target(
                            expected_handle=target_handle,
                            expected_pid=target_pid,
                            expected_title_regex=target_regex or step.get("title_regex"),
                            mismatch_mode=str(step.get("mismatch_mode", "refocus_and_retry")),
                            retries=_as_int(step.get("retries"), 3),
                            retry_wait_ms=_as_int(step.get("retry_wait_ms"), 150),
                            resolve_timeout_ms=_as_int(step.get("resolve_timeout_ms"), 4000),
                        )
                    elif action == "list_windows":
                        visible = _coerce_bool(step.get("only_visible", True))
                        wins = WINDOW_ENGINE.list_windows(only_visible=visible)
                        data = {"windows": wins, "count": len(wins)}
                    elif action == "launch_app":
                        command = str(step.get("command", ""))
                        pname = str(step.get("process_name_for_policy") or process_name_from_command(command))
                        assert_process_allowed(settings, pname)
                        data = WINDOW_ENGINE.launch_app(
                            command=command,
                            shell_mode=_coerce_bool(step.get("shell_mode", False)),
                            cwd=step.get("cwd"),
                            env=None,
                        )
                    elif action == "close_app":
                        pid = step.get("pid")
                        name_filter = step.get("name_filter")
                        if name_filter:
                            assert_process_allowed(settings, str(name_filter))
                        data = WINDOW_ENGINE.close_app(pid=int(pid) if pid is not None else None, name=name_filter)
                    elif action == "screenshot":
                        region = step.get("region")
                        data = SCREEN_ENGINE.capture(
                            path=step.get("path"),
                            monitor_index=_as_int(step.get("monitor_index"), 1),
                            region=region if isinstance(region, dict) else None,
                        )
                    elif action == "find_template":
                        region = step.get("region")
                        data = SCREEN_ENGINE.find_template(
                            template_path=str(step.get("template_path", "")),
                            monitor_index=_as_int(step.get("monitor_index"), 1),
                            region=region if isinstance(region, dict) else None,
                            threshold=float(step.get("threshold", 0.85)),
                            max_results=_as_int(step.get("max_results"), 5),
                            grayscale=_coerce_bool(step.get("grayscale", True)),
                        )
                    elif action == "click_template":
                        data = _click_by_template(step)
                        if not bool(data.get("matched", True)) and not settings.best_effort_vision:
                            raise RuntimeError(data.get("warning", "Template fallback could not find match"))
                    elif action in {"click_element", "element_click"}:
                        data = _click_element_with_fallback(step, session_id=resolved_session_id)
                    elif action in {"type_element", "element_type"}:
                        target_regex, target_handle, target_pid = _effective_target(step, session_id=resolved_session_id)
                        try:
                            data = WINDOW_ENGINE.type_element(
                                window_title_regex=target_regex,
                                text=str(step.get("text", "")),
                                title=step.get("title"),
                                auto_id=step.get("auto_id"),
                                control_type=step.get("control_type"),
                                clear_first=_coerce_bool(step.get("clear_first", False)),
                                found_index=_as_int(step.get("found_index"), 0),
                                window_handle=target_handle,
                                window_pid=target_pid,
                                timeout_ms=_as_int(step.get("timeout_ms"), 4000),
                            )
                            data = {"strategy": "uia", **data}
                        except Exception as uia_error:
                            click_data = _click_element_with_fallback(step, session_id=resolved_session_id)
                            typed = INPUT_ENGINE.type_text(
                                text=str(step.get("text", "")),
                                interval_ms=_as_int(step.get("interval_ms"), 0),
                            )
                            data = {
                                "strategy": "fallback_click_then_type",
                                "uia_error": str(uia_error),
                                "click": click_data,
                                "typed": typed,
                            }
                    elif action in {"element_action"}:
                        target_regex, target_handle, target_pid = _effective_target(step, session_id=resolved_session_id)
                        try:
                            data = WINDOW_ENGINE.perform_element_action(
                                action=str(step.get("element_action", step.get("target_action", "click"))),
                                window_title_regex=target_regex,
                                title=step.get("title"),
                                auto_id=step.get("auto_id"),
                                control_type=step.get("control_type"),
                                found_index=_as_int(step.get("found_index"), 0),
                                text=step.get("text"),
                                clear_first=_coerce_bool(step.get("clear_first", False)),
                                window_handle=target_handle,
                                window_pid=target_pid,
                                timeout_ms=_as_int(step.get("timeout_ms"), 4000),
                            )
                            data = {"strategy": "uia", **data}
                        except Exception as uia_error:
                            if not _coerce_bool(step.get("allow_fallback", True)):
                                raise
                            click_data = _click_element_with_fallback(step, session_id=resolved_session_id)
                            final_action = str(step.get("element_action", "click")).strip().lower()
                            data = {"strategy": "fallback_click", "uia_error": str(uia_error), "click": click_data}
                            if final_action in {"type", "clear_type"}:
                                typed = INPUT_ENGINE.type_text(
                                    text=str(step.get("text", "")),
                                    interval_ms=_as_int(step.get("interval_ms"), 0),
                                )
                                data["typed"] = typed
                    elif action == "wait_for":
                        timeout_ms = _as_int(step.get("timeout_ms"), 5000)
                        poll_ms = max(10, _as_int(step.get("poll_ms"), 200))
                        condition = str(step.get("condition", "exists"))
                        deadline = time.time() + max(0, timeout_ms) / 1000.0
                        attempts = 0
                        detail: dict[str, Any] = {}
                        while True:
                            attempts += 1
                            passed, detail = _check_element_condition(
                                step=step,
                                condition=condition,
                                session_id=resolved_session_id,
                            )
                            if passed:
                                data = {"condition": condition, "passed": True, "attempts": attempts, "detail": detail}
                                break
                            if time.time() >= deadline:
                                raise RuntimeError(f"Condition timed out: {condition}; detail={detail}")
                            INPUT_ENGINE.sleep(poll_ms)
                    elif action == "assert":
                        condition = str(step.get("condition", "exists"))
                        passed, detail = _check_element_condition(
                            step=step,
                            condition=condition,
                            session_id=resolved_session_id,
                        )
                        data = {"condition": condition, "passed": passed, "detail": detail}
                        if not passed:
                            raise RuntimeError(f"Assertion failed: {condition}")
                    elif action == "transaction_start":
                        data = TRANSACTION_ENGINE.start(
                            name=step.get("name_tag") or step.get("transaction_name"),
                            mode=str(step.get("mode", "fail")),
                            session_id=resolved_session_id,
                        )
                    elif action == "transaction_checkpoint":
                        region = step.get("region")
                        data = TRANSACTION_ENGINE.checkpoint(
                            label=step.get("label"),
                            monitor_index=_as_int(step.get("monitor_index"), 1),
                            region=region if isinstance(region, dict) else None,
                            session_id=resolved_session_id,
                        )
                    elif action == "transaction_rollback_hint":
                        data = TRANSACTION_ENGINE.rollback_hint(
                            reason=step.get("reason"),
                            max_hints=_as_int(step.get("max_hints"), 5),
                            session_id=resolved_session_id,
                        )
                    elif action == "transaction_end":
                        data = TRANSACTION_ENGINE.end(
                            committed=_coerce_bool(step.get("committed", True)),
                            summary=step.get("summary"),
                            session_id=resolved_session_id,
                        )
                    elif action == "transaction_status":
                        data = TRANSACTION_ENGINE.status(session_id=resolved_session_id)
                    elif action == "read_text":
                        backend = str(step.get("backend", default_ocr_backend)).strip().lower()
                        image_path = str(step.get("image_path", ""))
                        stabilization_attempts = max(1, _as_int(step.get("stabilization_attempts"), 3))
                        min_consensus = max(1, _as_int(step.get("min_consensus"), 2))
                        if backend == "rapidocr":
                            backend = "local_model"
                        if backend == "auto_with_host":
                            backend = "auto_with_host"
                        if backend == "auto":
                            data = pipeline.read_text_consensus(
                                image_path=image_path,
                                backend="auto",
                                lang=str(step.get("lang", "eng")),
                                attempts=stabilization_attempts,
                                min_consensus=min_consensus,
                                best_effort_ocr=settings.best_effort_ocr,
                            )
                        elif backend == "auto_with_host":
                            chain_notes: list[str] = []
                            if settings.enable_host_ocr and ctx is not None:
                                try:
                                    data = await _read_text_host_model(
                                        image_path=image_path,
                                        ctx=ctx,
                                        prompt=step.get("prompt"),
                                    )
                                    if data["chars"] > 0:
                                        data["fallback_notes"] = chain_notes
                                        # Success on host model first pass.
                                        pass
                                    else:
                                        raise RuntimeError("host_model returned empty text")
                                except Exception as e:
                                    chain_notes.append(f"host_model failed: {e}")
                                    data = pipeline.read_text_consensus(
                                        image_path=image_path,
                                        backend="auto",
                                        lang=str(step.get("lang", "eng")),
                                        attempts=stabilization_attempts,
                                        min_consensus=min_consensus,
                                        best_effort_ocr=settings.best_effort_ocr,
                                        fallback_notes=chain_notes,
                                    )
                            else:
                                chain_notes.append("host_model unavailable (disabled by policy or missing ctx)")
                                data = pipeline.read_text_consensus(
                                    image_path=image_path,
                                    backend="auto",
                                    lang=str(step.get("lang", "eng")),
                                    attempts=stabilization_attempts,
                                    min_consensus=min_consensus,
                                    best_effort_ocr=settings.best_effort_ocr,
                                    fallback_notes=chain_notes,
                                )
                        elif backend == "tesseract":
                            data = pipeline.read_text_consensus(
                                image_path=image_path,
                                backend="tesseract",
                                lang=str(step.get("lang", "eng")),
                                attempts=stabilization_attempts,
                                min_consensus=min_consensus,
                                best_effort_ocr=settings.best_effort_ocr,
                            )
                        elif backend in {"local_model", "rapidocr"}:
                            data = pipeline.read_text_consensus(
                                image_path=image_path,
                                backend="local_model",
                                lang=str(step.get("lang", "eng")),
                                attempts=stabilization_attempts,
                                min_consensus=min_consensus,
                                best_effort_ocr=settings.best_effort_ocr,
                            )
                        elif backend == "host_model":
                            if not settings.enable_host_ocr:
                                if settings.best_effort_ocr:
                                    data = {"text": "", "chars": 0, "backend": "none", "warning": "host_model OCR disabled by policy"}
                                else:
                                    raise RuntimeError("host_model OCR disabled by policy")
                            elif ctx is None:
                                if settings.best_effort_ocr:
                                    data = {"text": "", "chars": 0, "backend": "none", "warning": "host_model OCR unavailable: no MCP context"}
                                else:
                                    raise RuntimeError("host_model OCR unavailable: no MCP context")
                            else:
                                data = await _read_text_host_model(
                                    image_path=image_path,
                                    ctx=ctx,
                                    prompt=step.get("prompt"),
                                )
                        else:
                            if settings.best_effort_ocr:
                                data = {"text": "", "chars": 0, "backend": "none", "warning": f"Unsupported OCR backend: {backend}"}
                            else:
                                raise RuntimeError(f"Unsupported OCR backend: {backend}")
                    elif action == "assert_text":
                        image_path = str(step.get("image_path", ""))
                        expected = str(step.get("contains", ""))
                        if not expected:
                            raise RuntimeError("assert_text requires 'contains'")
                        ocr = pipeline.read_text_consensus(
                            image_path=image_path,
                            backend="local_model",
                            attempts=max(1, _as_int(step.get("stabilization_attempts"), 2)),
                            min_consensus=max(1, _as_int(step.get("min_consensus"), 1)),
                            best_effort_ocr=True,
                        )
                        text_lower = str(ocr.get("text", "")).lower()
                        ok_text = expected.lower() in text_lower
                        data = {"matched": ok_text, "contains": expected, "ocr_chars": ocr.get("chars", 0)}
                        if not ok_text:
                            raise RuntimeError(f"Expected text not found: {expected}")
                    elif action == "if":
                        cond_result = _evaluate_flow_condition(step, settings, session_id=resolved_session_id)
                        branch_steps = step.get("then", []) if cond_result else (step.get("otherwise") or step.get("else") or [])
                        branch_label = "then" if cond_result else "else"
                        if branch_steps:
                            sub_result = await execute_plan(
                                plan=branch_steps,
                                settings=settings,
                                stop_on_error=stop_on_error,
                                default_ocr_backend=default_ocr_backend,
                                on_error_generate_rollback_hint=on_error_generate_rollback_hint,
                                on_error_end_transaction=on_error_end_transaction,
                                on_error_max_hints=on_error_max_hints,
                                runtime_profile=runtime_profile,
                                runtime_options=runtime_options,
                                session_id=resolved_session_id,
                                ctx=ctx,
                                _depth=_depth + 1,
                            )
                            if not sub_result.get("ok") and stop_on_error:
                                raise RuntimeError(f"if/{branch_label} branch failed: {sub_result.get('error', {}).get('message', 'unknown')}")
                            data = {
                                "condition": step.get("condition"),
                                "evaluated": cond_result,
                                "branch": branch_label,
                                "sub_steps": sub_result.get("data", {}).get("steps_completed", 0),
                            }
                        else:
                            data = {
                                "condition": step.get("condition"),
                                "evaluated": cond_result,
                                "branch": branch_label,
                                "skipped": True,
                            }
                    elif action == "repeat":
                        count = max(0, _as_int(step.get("count"), 0))
                        repeat_steps = step.get("steps", [])
                        iterations_completed = 0
                        for i in range(count):
                            sub_result = await execute_plan(
                                plan=repeat_steps,
                                settings=settings,
                                stop_on_error=stop_on_error,
                                default_ocr_backend=default_ocr_backend,
                                on_error_generate_rollback_hint=on_error_generate_rollback_hint,
                                on_error_end_transaction=on_error_end_transaction,
                                on_error_max_hints=on_error_max_hints,
                                runtime_profile=runtime_profile,
                                runtime_options=runtime_options,
                                session_id=resolved_session_id,
                                ctx=ctx,
                                _depth=_depth + 1,
                            )
                            iterations_completed += 1
                            if not sub_result.get("ok") and stop_on_error:
                                raise RuntimeError(f"repeat iteration {i + 1} failed")
                        data = {"count": count, "iterations_completed": iterations_completed}
                    elif action == "while":
                        max_iter = max(1, _as_int(step.get("max_iterations"), 100))
                        while_steps = step.get("steps", [])
                        iterations_completed = 0
                        for i in range(max_iter):
                            cond_result = _evaluate_flow_condition(step, settings, session_id=resolved_session_id)
                            if not cond_result:
                                break
                            sub_result = await execute_plan(
                                plan=while_steps,
                                settings=settings,
                                stop_on_error=stop_on_error,
                                default_ocr_backend=default_ocr_backend,
                                on_error_generate_rollback_hint=on_error_generate_rollback_hint,
                                on_error_end_transaction=on_error_end_transaction,
                                on_error_max_hints=on_error_max_hints,
                                runtime_profile=runtime_profile,
                                runtime_options=runtime_options,
                                session_id=resolved_session_id,
                                ctx=ctx,
                                _depth=_depth + 1,
                            )
                            iterations_completed += 1
                            if not sub_result.get("ok") and stop_on_error:
                                raise RuntimeError(f"while iteration {i + 1} failed")
                        data = {
                            "condition": step.get("condition"),
                            "max_iterations": max_iter,
                            "iterations_completed": iterations_completed,
                        }
                    else:
                        if runtime.policy.allow_unknown_actions:
                            data = {
                                "warning": "Unsupported action mapped to no-op",
                                "requested_action": action,
                            }
                        else:
                            raise RuntimeError(f"Unsupported action: {action}")

                    if _is_mutating_action(action):
                        post_guard = _guard_foreground_for_step(
                            step=step,
                            action=action,
                            session_id=resolved_session_id,
                        )
                        if pre_guard is not None or post_guard is not None:
                            data["foreground"] = {"pre": pre_guard, "post": post_guard}
                    if runtime.should_observe(action):
                        try:
                            post_observation = {
                                "foreground": WINDOW_ENGINE.get_foreground_window(),
                                "state_handoff": runtime.state_handoff(),
                            }
                        except Exception as obs_e:
                            post_observation = {"warning": str(obs_e)}

                    strategy_path = [action]
                    if isinstance(data, dict):
                        if canary_report is not None:
                            data["canary"] = canary_report
                        if fallback_policy_notes:
                            data["fallback_policy_notes"] = fallback_policy_notes
                        route_note = broker_route_note
                        if isinstance(route_note, dict):
                            data["broker_route_note"] = route_note
                            strategy_path.append("broker_fallback_local")
                        strategy = data.get("strategy")
                        if isinstance(strategy, str) and strategy:
                            strategy_path.append(strategy)
                        if isinstance(data.get("fallback_notes"), list) and data.get("fallback_notes"):
                            strategy_path.append("fallback")

                    final_result = runtime.canonical_step(
                        name=name,
                        action=action,
                        attempt=attempt,
                        ok=True,
                        data=data,
                        pre_observation=pre_observation,
                        post_observation=post_observation,
                        strategy_path=strategy_path,
                    )
                    runtime.append_transcript(name=name, action=action, params=step, result=final_result)
                    break
                except Exception as e:
                    last_error = e
                    if attempt < retries:
                        INPUT_ENGINE.sleep(_as_int(step.get("retry_wait_ms"), 250))
                        continue

            if final_result is None:
                failure = _step_error(step={"name": name, "action": action}, e=last_error or RuntimeError("unknown"), started=started)
                if stop_on_error:
                    reason = f"Step '{name}' failed: {last_error}"
                    recovery = _transaction_failure_recovery(
                        reason=reason,
                        generate_rollback_hint=on_error_generate_rollback_hint,
                        end_transaction=on_error_end_transaction,
                        max_hints=on_error_max_hints,
                        session_id=resolved_session_id,
                    )
                    return {
                        "ok": False,
                        "action": "desktop_act",
                        "error": {
                            **failure["error"],
                            "code": _classify_error(last_error or RuntimeError("unknown")),
                            "hint": runtime.error_hint(_classify_error(last_error or RuntimeError("unknown"))),
                        },
                        "data": {
                            "steps": steps,
                            "transaction_recovery": recovery,
                            "preflight": preflight,
                            "runtime": {
                                "profile": runtime.policy.profile,
                                "state_handoff": runtime.state_handoff(),
                                "transcript": runtime.transcript,
                            },
                        },
                        "timing_ms": failure.get("timing_ms", 0),
                    }
                code = _classify_error(last_error or RuntimeError("unknown"))
                step_error = {
                    "code": code,
                    "message": str(last_error or "unknown"),
                    "hint": runtime.error_hint(code),
                    "details": {},
                }
                failed_step = runtime.canonical_step(
                    name=name,
                    action=action,
                    attempt=retries,
                    ok=False,
                    error=step_error,
                    strategy_path=[action, "failed"],
                )
                steps.append(failed_step)
                runtime.append_transcript(name=name, action=action, params=step, result=failed_step)
                continue

            steps.append(final_result)
            if wait_after_ms > 0:
                INPUT_ENGINE.sleep(wait_after_ms)

        success = all(bool(s.get("ok")) for s in steps)
        payload = {
            "steps": steps,
            "count": len(steps),
            "succeeded": success,
            "preflight": preflight,
            "runtime": {
                "profile": runtime.policy.profile,
                "state_handoff": runtime.state_handoff(),
                "transcript": runtime.transcript,
            },
        }
        if success:
            return ok("desktop_act", data=payload, started_ms=started)
        recovery = _transaction_failure_recovery(
            reason="One or more steps failed",
            generate_rollback_hint=on_error_generate_rollback_hint,
            end_transaction=on_error_end_transaction,
            max_hints=on_error_max_hints,
            session_id=resolved_session_id,
        )
        payload["transaction_recovery"] = recovery
        return {
            "ok": False,
            "action": "desktop_act",
            "error": {"code": "partial_failure", "message": "One or more steps failed", "hint": "", "details": {}},
            "data": payload,
            "timing_ms": now_ms() - started,
        }
    except Exception as e:
        recovery = _transaction_failure_recovery(
            reason=f"desktop_act internal error: {e}",
            generate_rollback_hint=on_error_generate_rollback_hint,
            end_transaction=on_error_end_transaction,
            max_hints=on_error_max_hints,
            session_id=resolved_session_id,
        )
        res = err("desktop_act", str(e), started_ms=started, code=_classify_error(e))
        code = res.get("error", {}).get("code", "runtime_error")
        if isinstance(res.get("error"), dict):
            res["error"]["hint"] = runtime.error_hint(str(code))
        res["data"] = {
            "transaction_recovery": recovery,
            "runtime": {
                "profile": runtime.policy.profile,
                "state_handoff": runtime.state_handoff(),
                "transcript": runtime.transcript,
            },
        }
        return res


def register_orchestration_tools(mcp: FastMCP) -> None:
    @mcp.tool(description="Execute a multi-step automation plan. Each step is a dict with an 'action' field (click, type, scroll, hotkey, key_press, focus_window, screenshot, wait, etc.). Supports retries, foreground guards, transactions, and runtime profiles (basic_reliable, strict, balanced, unrestricted).")
    async def desktop_act(
        plan: list[dict[str, Any]],
        stop_on_error: bool = True,
        default_ocr_backend: str = "auto",
        on_error_generate_rollback_hint: bool = True,
        on_error_end_transaction: bool = True,
        on_error_max_hints: int = 5,
        runtime_profile: str = "basic_reliable",
        runtime_options: dict[str, Any] | None = None,
        enforce_preflight: bool = False,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        settings = load_settings()
        resolved_session_id = STATE.resolve_session_id(session_id)
        result = await execute_plan(
            plan=plan,
            settings=settings,
            stop_on_error=stop_on_error,
            default_ocr_backend=default_ocr_backend,
            on_error_generate_rollback_hint=on_error_generate_rollback_hint,
            on_error_end_transaction=on_error_end_transaction,
            on_error_max_hints=on_error_max_hints,
            runtime_profile=runtime_profile,
            runtime_options=runtime_options,
            enforce_preflight=enforce_preflight,
            session_id=resolved_session_id,
            ctx=ctx,
        )
        if result.get("ok"):
            STATE.recording_append(
                action="desktop_act",
                params={
                    "plan": plan,
                    "stop_on_error": stop_on_error,
                    "default_ocr_backend": default_ocr_backend,
                    "on_error_generate_rollback_hint": on_error_generate_rollback_hint,
                    "on_error_end_transaction": on_error_end_transaction,
                    "on_error_max_hints": on_error_max_hints,
                    "runtime_profile": runtime_profile,
                    "runtime_options": runtime_options,
                    "enforce_preflight": enforce_preflight,
                    "session_id": resolved_session_id,
                },
                data={"succeeded": result.get("data", {}).get("succeeded", False)},
                session_id=resolved_session_id,
            )
        return result


