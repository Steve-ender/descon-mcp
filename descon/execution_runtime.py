from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from descon.result import now_ms
from descon.state import STATE


RISKY_ACTIONS = {
    "click",
    "type",
    "hotkey",
    "key_press",
    "mouse_down",
    "mouse_up",
    "drag_to",
    "launch_app",
    "close_app",
    "click_template",
    "click_element",
    "element_click",
    "type_element",
    "element_type",
    "element_action",
}

DESTRUCTIVE_ACTIONS = {
    "launch_app",
    "close_app",
}

FAILURE_HINTS: dict[str, str] = {
    "session_inactive": "Start or resume session before running actions.",
    "permission_denied": "Check elevation/admin mismatch or UIPI restrictions.",
    "foreground_mismatch": "Bind/focus target window before mutating actions.",
    "foreground_unresolvable_target": "Provide window_handle/window_pid/title_regex or bind a window.",
    "not_found": "Re-check locators/regex and UI state before retry.",
    "timeout": "Increase timeout/poll settings or add explicit wait_for steps.",
    "policy_blocked": "Adjust policy profile or allowlist configuration.",
    "validation_error": "Fix step schema/arguments before retry.",
    "broker_unavailable": "Start/configure the elevated broker or run without broker routing.",
    "runtime_error": "Inspect step transcript and latest observation.",
}


@dataclass
class RuntimePolicy:
    profile: str = "balanced"
    max_steps: int = 200
    max_runtime_ms: int = 600000
    default_retries: int = 2
    observe_before_risky: bool = True
    safe_mode: bool = False
    allow_unknown_actions: bool = True
    canary_checks: bool = True
    auto_fallback: bool = True
    fallback_policy: str = "conservative"


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


def policy_from_options(profile: str = "balanced", options: dict[str, Any] | None = None) -> RuntimePolicy:
    p = (profile or "balanced").strip().lower()
    if p == "basic_reliable":
        base = RuntimePolicy(
            profile="basic_reliable",
            max_steps=250,
            max_runtime_ms=900000,
            default_retries=2,
            observe_before_risky=True,
            safe_mode=True,
            allow_unknown_actions=False,
            canary_checks=True,
            auto_fallback=True,
            fallback_policy="conservative",
        )
    elif p == "strict":
        base = RuntimePolicy(
            profile="strict",
            max_steps=120,
            max_runtime_ms=300000,
            default_retries=1,
            observe_before_risky=True,
            safe_mode=True,
            allow_unknown_actions=False,
            canary_checks=True,
            auto_fallback=False,
            fallback_policy="none",
        )
    elif p == "unrestricted":
        base = RuntimePolicy(
            profile="unrestricted",
            max_steps=5000,
            max_runtime_ms=7200000,
            default_retries=3,
            observe_before_risky=False,
            safe_mode=False,
            allow_unknown_actions=True,
            canary_checks=False,
            auto_fallback=False,
            fallback_policy="none",
        )
    else:
        base = RuntimePolicy(
            canary_checks=True,
            auto_fallback=True,
            fallback_policy="conservative",
        )
    opts = options or {}
    for k in ["max_steps", "max_runtime_ms", "default_retries"]:
        if k in opts:
            try:
                setattr(base, k, max(1, int(opts[k])))
            except Exception:
                pass
    for k in ["observe_before_risky", "safe_mode", "allow_unknown_actions", "canary_checks", "auto_fallback"]:
        if k in opts:
            setattr(base, k, _coerce_bool(opts[k]))
    if "fallback_policy" in opts:
        fp = str(opts.get("fallback_policy", "")).strip().lower()
        if fp in {"none", "conservative", "aggressive"}:
            base.fallback_policy = fp
    return base


class RuntimeController:
    def __init__(self, policy: RuntimePolicy, session_id: str | None = None) -> None:
        self.policy = policy
        self.started_ms = now_ms()
        self.transcript: list[dict[str, Any]] = []
        self.session_id = STATE.resolve_session_id(session_id)

    def state_handoff(self) -> dict[str, Any]:
        st = STATE.get(session_id=self.session_id)
        return {
            "active": st.active,
            "paused": st.paused,
            "cancel_requested": st.cancel_requested,
            "action_count": st.action_count,
            "focused_window_title": st.focused_window_title,
            "focused_window_handle": st.focused_window_handle,
            "focused_window_pid": st.focused_window_pid,
            "bound_window_title_regex": st.bound_window_title_regex,
            "bound_window_handle": st.bound_window_handle,
            "bound_window_pid": st.bound_window_pid,
        }

    def check_budget(self, current_step: int) -> None:
        if current_step >= self.policy.max_steps:
            raise RuntimeError(f"Step budget exceeded: {self.policy.max_steps}")
        elapsed = now_ms() - self.started_ms
        if elapsed >= self.policy.max_runtime_ms:
            raise RuntimeError(f"Runtime budget exceeded: {self.policy.max_runtime_ms}ms")

    def ensure_allowed_action(self, action: str, confirmed: bool) -> None:
        if self.policy.safe_mode and action in DESTRUCTIVE_ACTIONS and not confirmed:
            raise RuntimeError(f"Safe mode requires explicit confirm=true for action: {action}")

    def should_observe(self, action: str) -> bool:
        return self.policy.observe_before_risky and action in RISKY_ACTIONS

    def canonical_step(
        self,
        *,
        name: str,
        action: str,
        attempt: int,
        ok: bool,
        data: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        pre_observation: dict[str, Any] | None = None,
        post_observation: dict[str, Any] | None = None,
        confirmation: dict[str, Any] | None = None,
        strategy_path: list[str] | None = None,
    ) -> dict[str, Any]:
        confirmation_out = confirmation or self.default_confirmation(
            action=action,
            ok=bool(ok),
            data=data or {},
            pre_observation=pre_observation,
            post_observation=post_observation,
        )
        out = {
            "name": name,
            "action": action,
            "attempt": attempt,
            "ok": bool(ok),
            "data": data or {},
            "error": error or None,
            "observation": {
                "pre": pre_observation,
                "post": post_observation,
            },
            "confirmation": confirmation_out,
            "runtime_meta": {
                "profile": self.policy.profile,
                "strategy_path": strategy_path or [action],
            },
        }
        return out

    def append_transcript(
        self,
        *,
        name: str,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        blob = json.dumps({"name": name, "action": action, "params": params, "result": result}, sort_keys=True, separators=(",", ":"))
        fp = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]
        self.transcript.append(
            {
                "ts_ms": now_ms(),
                "name": name,
                "action": action,
                "fingerprint": fp,
                "params": params,
                "result": result,
            }
        )

    def error_hint(self, code: str) -> str:
        return FAILURE_HINTS.get(code, FAILURE_HINTS["runtime_error"])

    def default_confirmation(
        self,
        *,
        action: str,
        ok: bool,
        data: dict[str, Any],
        pre_observation: dict[str, Any] | None,
        post_observation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not ok:
            return {"status": "failed", "reason": "action_failed", "evidence": {}}
        evidence: dict[str, Any] = {}
        if isinstance(pre_observation, dict):
            evidence["pre"] = pre_observation
        if isinstance(post_observation, dict):
            evidence["post"] = post_observation
        if action in RISKY_ACTIONS:
            if pre_observation and post_observation:
                return {"status": "confirmed", "reason": "pre_post_observed", "evidence": evidence}
            return {"status": "partial", "reason": "observation_missing", "evidence": evidence}
        return {"status": "confirmed", "reason": "non_risky_action", "evidence": evidence}
