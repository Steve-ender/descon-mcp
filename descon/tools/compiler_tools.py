from __future__ import annotations

from typing import Any

from descon.result import err, now_ms


PROFILE_DEFAULTS: dict[str, dict[str, int]] = {
    "fast": {
        "retries": 1,
        "retry_wait_ms": 100,
        "wait_after_ms": 0,
        "timeout_ms": 2000,
        "poll_ms": 100,
    },
    "balanced": {
        "retries": 2,
        "retry_wait_ms": 250,
        "wait_after_ms": 40,
        "timeout_ms": 5000,
        "poll_ms": 200,
    },
    "stubborn": {
        "retries": 4,
        "retry_wait_ms": 400,
        "wait_after_ms": 80,
        "timeout_ms": 12000,
        "poll_ms": 250,
    },
}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
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


def _profile(intent: dict[str, Any], default_profile: str) -> tuple[str, dict[str, int]]:
    p = str(intent.get("profile", default_profile)).strip().lower()
    if p not in PROFILE_DEFAULTS:
        p = default_profile
    base = PROFILE_DEFAULTS[p].copy()
    base["retries"] = _as_int(intent.get("retries"), base["retries"])
    base["retry_wait_ms"] = _as_int(intent.get("retry_wait_ms"), base["retry_wait_ms"])
    base["wait_after_ms"] = _as_int(intent.get("wait_after_ms"), base["wait_after_ms"])
    base["timeout_ms"] = _as_int(intent.get("timeout_ms"), base["timeout_ms"])
    base["poll_ms"] = _as_int(intent.get("poll_ms"), base["poll_ms"])
    return p, base


def _locator(intent: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "window_title_regex": intent.get("window_title_regex"),
        "window_handle": intent.get("window_handle"),
        "window_pid": intent.get("window_pid"),
        "title": intent.get("title"),
        "auto_id": intent.get("auto_id"),
        "control_type": intent.get("control_type"),
        "found_index": _as_int(intent.get("found_index"), 0),
        "timeout_ms": _as_int(intent.get("timeout_ms"), 0) or None,
    }
    return {k: v for k, v in out.items() if v is not None}


def _fallback_chain(intent: dict[str, Any]) -> list[str]:
    chain = ["uia"]
    if _coerce_bool(intent.get("allow_fallback", True), default=True):
        if intent.get("template_path"):
            chain.append("template")
        if intent.get("text_query"):
            chain.append("ocr_text")
    return chain


def _foreground_policy(intent: dict[str, Any], default_required: bool = True) -> dict[str, Any]:
    return {
        "require_foreground_match": _coerce_bool(intent.get("require_foreground_match", default_required), default=default_required),
        "foreground_mismatch_mode": str(intent.get("foreground_mismatch_mode", "refocus_and_retry")),
        "foreground_retries": _as_int(intent.get("foreground_retries"), 3),
        "foreground_retry_wait_ms": _as_int(intent.get("foreground_retry_wait_ms"), 150),
    }


def _compile_intent(intent: dict[str, Any], idx: int, default_profile: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    intent_type = str(intent.get("intent", "")).strip().lower()
    if not intent_type:
        raise RuntimeError(f"Intent at index {idx} missing 'intent'")

    profile_name, p = _profile(intent, default_profile=default_profile)
    step_prefix = f"s{idx + 1:03d}"
    steps: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"intent": intent_type, "profile": profile_name, "steps": []}

    if intent_type == "wait":
        step = {
            "name": f"{step_prefix}_wait",
            "action": "wait",
            "time_ms": _as_int(intent.get("time_ms"), 250),
        }
        steps.append(step)
    elif intent_type == "focus_window":
        title_regex = intent.get("title_regex", intent.get("window_title_regex"))
        step = {
            "name": f"{step_prefix}_focus_window",
            "action": "focus_window",
            "title_regex": str(title_regex) if title_regex is not None else None,
            "window_handle": intent.get("window_handle"),
            "window_pid": intent.get("window_pid"),
            "timeout_ms": _as_int(intent.get("timeout_ms"), 0) or None,
            "retries": p["retries"],
            "retry_wait_ms": p["retry_wait_ms"],
            "wait_after_ms": p["wait_after_ms"],
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type in {"click_element", "type_element", "element_action"}:
        element_action = str(intent.get("element_action", "click"))
        if intent_type == "click_element":
            element_action = "click"
        elif intent_type == "type_element":
            element_action = "type"
        step = {
            "name": f"{step_prefix}_element_action",
            "action": "element_action",
            "element_action": element_action,
            **_locator(intent),
            **_foreground_policy(intent, default_required=True),
            "text": intent.get("text"),
            "clear_first": _coerce_bool(intent.get("clear_first", False), default=False),
            "allow_fallback": _coerce_bool(intent.get("allow_fallback", True), default=True),
            "template_path": intent.get("template_path"),
            "text_query": intent.get("text_query"),
            "monitor_index": _as_int(intent.get("monitor_index"), 1),
            "threshold": float(intent.get("threshold", 0.85)),
            "button": str(intent.get("button", "left")),
            "clicks": _as_int(intent.get("clicks"), 1),
            "interval_ms": _as_int(intent.get("interval_ms"), 0),
            "retries": p["retries"],
            "retry_wait_ms": p["retry_wait_ms"],
            "wait_after_ms": p["wait_after_ms"],
            "fallback_chain": _fallback_chain(intent),
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type in {"mouse_down", "mouse_up"}:
        step = {
            "name": f"{step_prefix}_{intent_type}",
            "action": intent_type,
            "x": intent.get("x"),
            "y": intent.get("y"),
            "button": str(intent.get("button", "left")),
            **_foreground_policy(intent, default_required=True),
            "retries": p["retries"],
            "retry_wait_ms": p["retry_wait_ms"],
            "wait_after_ms": p["wait_after_ms"],
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "drag_to":
        step = {
            "name": f"{step_prefix}_drag_to",
            "action": "drag_to",
            "x": _as_int(intent.get("x"), 0),
            "y": _as_int(intent.get("y"), 0),
            "duration_ms": _as_int(intent.get("duration_ms"), 200),
            "button": str(intent.get("button", "left")),
            "mouse_down_up": _coerce_bool(intent.get("mouse_down_up", True), default=True),
            **_foreground_policy(intent, default_required=True),
            "retries": p["retries"],
            "retry_wait_ms": p["retry_wait_ms"],
            "wait_after_ms": p["wait_after_ms"],
        }
        steps.append(step)
    elif intent_type == "hotkey":
        step = {
            "name": f"{step_prefix}_hotkey",
            "action": "hotkey",
            "keys": [str(k) for k in (intent.get("keys") or [])],
            **_foreground_policy(intent, default_required=True),
            "retries": p["retries"],
            "retry_wait_ms": p["retry_wait_ms"],
            "wait_after_ms": p["wait_after_ms"],
        }
        steps.append(step)
    elif intent_type == "key_press":
        step = {
            "name": f"{step_prefix}_key_press",
            "action": "key_press",
            "key": str(intent.get("key", "")),
            "presses": _as_int(intent.get("presses"), 1),
            "interval_ms": _as_int(intent.get("interval_ms"), 0),
            **_foreground_policy(intent, default_required=True),
            "retries": p["retries"],
            "retry_wait_ms": p["retry_wait_ms"],
            "wait_after_ms": p["wait_after_ms"],
        }
        steps.append(step)
    elif intent_type == "launch_app":
        step = {
            "name": f"{step_prefix}_launch_app",
            "action": "launch_app",
            "confirm": _coerce_bool(intent.get("confirm", True), default=True),
            "command": str(intent.get("command", "")),
            "process_name_for_policy": intent.get("process_name_for_policy"),
            "shell_mode": _coerce_bool(intent.get("shell_mode", False), default=False),
            "cwd": intent.get("cwd"),
            "retries": p["retries"],
            "retry_wait_ms": p["retry_wait_ms"],
            "wait_after_ms": p["wait_after_ms"],
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "close_app":
        step = {
            "name": f"{step_prefix}_close_app",
            "action": "close_app",
            "confirm": _coerce_bool(intent.get("confirm", True), default=True),
            "pid": intent.get("pid"),
            "name_filter": intent.get("name_filter"),
            "retries": p["retries"],
            "retry_wait_ms": p["retry_wait_ms"],
            "wait_after_ms": p["wait_after_ms"],
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "wait_for":
        step = {
            "name": f"{step_prefix}_wait_for",
            "action": "wait_for",
            "condition": str(intent.get("condition", "exists")),
            **_locator(intent),
            "text_query": intent.get("text_query"),
            "timeout_ms": p["timeout_ms"],
            "poll_ms": p["poll_ms"],
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "assert":
        step = {
            "name": f"{step_prefix}_assert",
            "action": "assert",
            "condition": str(intent.get("condition", "exists")),
            **_locator(intent),
            "text_query": intent.get("text_query"),
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "screenshot":
        region = intent.get("region")
        step = {
            "name": f"{step_prefix}_screenshot",
            "action": "screenshot",
            "path": intent.get("path"),
            "monitor_index": _as_int(intent.get("monitor_index"), 1),
            "region": region if isinstance(region, dict) else None,
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "read_text":
        step = {
            "name": f"{step_prefix}_read_text",
            "action": "read_text",
            "image_path": str(intent.get("image_path", "")),
            "backend": str(intent.get("backend", "auto")),
            "lang": str(intent.get("lang", "eng")),
        }
        steps.append(step)
    elif intent_type == "transaction_start":
        step = {
            "name": f"{step_prefix}_transaction_start",
            "action": "transaction_start",
            "transaction_name": intent.get("transaction_name", intent.get("name")),
            "mode": str(intent.get("mode", "fail")),
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "transaction_checkpoint":
        region = intent.get("region")
        step = {
            "name": f"{step_prefix}_transaction_checkpoint",
            "action": "transaction_checkpoint",
            "label": intent.get("label"),
            "monitor_index": _as_int(intent.get("monitor_index"), 1),
            "region": region if isinstance(region, dict) else None,
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "transaction_rollback_hint":
        step = {
            "name": f"{step_prefix}_transaction_rollback_hint",
            "action": "transaction_rollback_hint",
            "reason": intent.get("reason"),
            "max_hints": _as_int(intent.get("max_hints"), 5),
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "transaction_end":
        step = {
            "name": f"{step_prefix}_transaction_end",
            "action": "transaction_end",
            "committed": _coerce_bool(intent.get("committed", True), default=True),
            "summary": intent.get("summary"),
        }
        steps.append({k: v for k, v in step.items() if v is not None})
    elif intent_type == "transaction_status":
        step = {
            "name": f"{step_prefix}_transaction_status",
            "action": "transaction_status",
        }
        steps.append(step)
    else:
        raise RuntimeError(f"Unsupported intent type: {intent_type}")

    if "post_assert" in intent and isinstance(intent["post_assert"], dict):
        pa = intent["post_assert"]
        assert_step = {
            "name": f"{step_prefix}_post_assert",
            "action": "assert",
            "condition": str(pa.get("condition", "exists")),
            **_locator({**intent, **pa}),
            "text_query": pa.get("text_query"),
        }
        steps.append({k: v for k, v in assert_step.items() if v is not None})

    meta["steps"] = [s.get("name", "") for s in steps]
    if any(s.get("action") == "element_action" for s in steps):
        meta["fallback_chain"] = _fallback_chain(intent)
    return steps, meta


def _is_mutating_intent(intent_type: str) -> bool:
    return intent_type in {
        "click_element",
        "type_element",
        "element_action",
        "hotkey",
        "key_press",
        "mouse_down",
        "mouse_up",
        "drag_to",
        "launch_app",
        "close_app",
    }


def _is_transaction_intent(intent_type: str) -> bool:
    return intent_type in {
        "transaction_start",
        "transaction_checkpoint",
        "transaction_rollback_hint",
        "transaction_end",
        "transaction_status",
    }


def compile_intents(
    intents: list[dict[str, Any]],
    profile: str = "balanced",
    stop_on_error: bool = True,
    default_ocr_backend: str = "auto",
    wrap_in_transaction: bool = False,
    transaction_name: str | None = None,
    transaction_start_mode: str = "fail",
    auto_checkpoints: str = "major",
    include_metadata: bool = True,
) -> dict[str, Any]:
    started = now_ms()
    if not intents:
        return err("desktop_recording", "No intents provided", started_ms=started)

    profile_name = profile.strip().lower()
    if profile_name not in PROFILE_DEFAULTS:
        return err(
            "desktop_recording",
            f"Unsupported profile: {profile}",
            hint=f"Use one of: {', '.join(sorted(PROFILE_DEFAULTS.keys()))}",
            started_ms=started,
        )

    cp_mode = auto_checkpoints.strip().lower()
    if cp_mode not in {"none", "major", "all"}:
        return err(
            "desktop_recording",
            f"Unsupported auto_checkpoints mode: {auto_checkpoints}",
            hint="Use one of: none, major, all",
            started_ms=started,
        )
    tx_mode = transaction_start_mode.strip().lower()
    if tx_mode not in {"fail", "reuse", "restart"}:
        return err(
            "desktop_recording",
            f"Unsupported transaction_start_mode: {transaction_start_mode}",
            hint="Use one of: fail, reuse, restart",
            started_ms=started,
        )

    intent_types = []
    for i, intent in enumerate(intents):
        if not isinstance(intent, dict):
            raise RuntimeError(f"Intent at index {i} must be an object")
        intent_types.append(str(intent.get("intent", "")).strip().lower())
    has_explicit_tx = any(_is_transaction_intent(t) for t in intent_types)
    if wrap_in_transaction and has_explicit_tx:
        return err(
            "desktop_recording",
            "Conflicting transaction strategy",
            hint=(
                "Do not combine wrap_in_transaction=true with explicit transaction_* intents. "
                "Choose one strategy."
            ),
            started_ms=started,
        )

    compiled: list[dict[str, Any]] = []
    compile_log: list[dict[str, Any]] = []
    if wrap_in_transaction:
        compiled.append(
            {
                "name": "s000_transaction_start",
                "action": "transaction_start",
                "transaction_name": transaction_name,
                "mode": tx_mode,
            }
        )
    for i, intent in enumerate(intents):
        steps, meta = _compile_intent(intent=intent, idx=i, default_profile=profile_name)
        compiled.extend(steps)
        compile_log.append(meta)
        intent_type = str(intent.get("intent", "")).strip().lower()
        if wrap_in_transaction and cp_mode != "none":
            if cp_mode == "all" or _is_mutating_intent(intent_type):
                compiled.append(
                    {
                        "name": f"s{i + 1:03d}_tx_checkpoint",
                        "action": "transaction_checkpoint",
                        "label": f"after_{intent_type or 'intent'}_{i + 1:03d}",
                        "monitor_index": _as_int(intent.get("monitor_index"), 1),
                    }
                )
    if wrap_in_transaction:
        compiled.append(
            {
                "name": "s999_transaction_end",
                "action": "transaction_end",
                "committed": True,
                "summary": "Compiled plan completed",
            }
        )

    data: dict[str, Any] = {
        "plan": compiled,
        "count": len(compiled),
        "execution": {
            "stop_on_error": stop_on_error,
            "default_ocr_backend": default_ocr_backend,
        },
        "profile": profile_name,
        "transaction": {
            "wrapped": wrap_in_transaction,
            "name": transaction_name,
            "start_mode": tx_mode,
            "auto_checkpoints": cp_mode,
        },
    }
    if include_metadata:
        data["compile_log"] = compile_log
        data["profiles"] = PROFILE_DEFAULTS
    return data
