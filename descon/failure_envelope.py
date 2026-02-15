from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from descon.config import Settings
from descon.execution_runtime import DESTRUCTIVE_ACTIONS, RuntimeController, policy_from_options
from descon.safety import normalize_process_name, process_name_from_command


@dataclass
class EnvelopeIssue:
    code: str
    severity: str
    message: str
    step_index: int | None = None
    action: str | None = None
    hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "step_index": self.step_index,
            "action": self.action,
            "hint": self.hint,
        }


def _action(step: dict[str, Any]) -> str:
    return str(step.get("action") or "").strip().lower()


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


def analyze_failure_envelope(
    *,
    plan: list[dict[str, Any]],
    settings: Settings,
    runtime_profile: str = "basic_reliable",
    runtime_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = RuntimeController(policy_from_options(profile=runtime_profile, options=runtime_options))
    opts = runtime_options or {}
    issues: list[EnvelopeIssue] = []
    hard_blockers: list[EnvelopeIssue] = []
    warnings: list[EnvelopeIssue] = []
    broker_mode = str(opts.get("broker_mode", "auto")).strip().lower()
    if broker_mode not in {"auto", "never", "always"}:
        warnings.append(
            EnvelopeIssue(
                code="invalid_broker_mode",
                severity="medium",
                message=f"Unknown broker_mode '{broker_mode}'. Using 'auto'.",
                hint="Set runtime_options.broker_mode to one of: auto, never, always.",
            )
        )
        broker_mode = "auto"

    if not plan:
        blocker = EnvelopeIssue(
            code="plan_empty",
            severity="high",
            message="Plan is empty.",
            hint="Provide at least one executable action.",
        )
        hard_blockers.append(blocker)
        issues.append(blocker)

    if broker_mode == "always" and not settings.enable_elevated_broker:
        blocker = EnvelopeIssue(
            code="broker_mode_always_without_broker",
            severity="high",
            message="runtime_options.broker_mode=always but elevated broker is disabled.",
            hint="Enable DESCON_ENABLE_ELEVATED_BROKER=1 or set broker_mode to auto/never.",
        )
        hard_blockers.append(blocker)
        issues.append(blocker)

    for idx, step in enumerate(plan):
        if not isinstance(step, dict):
            blocker = EnvelopeIssue(
                code="invalid_step_type",
                severity="high",
                message=f"Step at index {idx} must be an object.",
                step_index=idx,
                action="unknown",
                hint="Replace non-object step with a valid action object.",
            )
            hard_blockers.append(blocker)
            issues.append(blocker)
            continue
        action = _action(step)
        if not action:
            blocker = EnvelopeIssue(
                code="missing_action",
                severity="high",
                message="Step is missing required 'action'.",
                step_index=idx,
                action="unknown",
                hint="Set a valid action name in this step.",
            )
            hard_blockers.append(blocker)
            issues.append(blocker)
            continue

        if runtime.policy.safe_mode and action in DESTRUCTIVE_ACTIONS and not _coerce_bool(step.get("confirm", False)):
            blocker = EnvelopeIssue(
                code="safe_mode_confirm_required",
                severity="high",
                message=f"Safe mode requires confirm=true for destructive action '{action}'.",
                step_index=idx,
                action=action,
                hint="Add confirm=true or use a non-safe-mode runtime profile intentionally.",
            )
            hard_blockers.append(blocker)
            issues.append(blocker)

        force_broker = _coerce_bool(step.get("force_broker"))
        force_local = _coerce_bool(step.get("force_local"))

        if force_broker and not settings.enable_elevated_broker:
            blocker = EnvelopeIssue(
                code="force_broker_without_broker",
                severity="high",
                message="Step requests force_broker, but elevated broker is disabled.",
                step_index=idx,
                action=action,
                hint="Enable DESCON_ENABLE_ELEVATED_BROKER=1 or remove force_broker.",
            )
            hard_blockers.append(blocker)
            issues.append(blocker)

        if force_broker and force_local:
            blocker = EnvelopeIssue(
                code="conflicting_routing_directives",
                severity="high",
                message="Step sets both force_broker and force_local.",
                step_index=idx,
                action=action,
                hint="Set only one routing directive per step.",
            )
            hard_blockers.append(blocker)
            issues.append(blocker)

        if action in {"launch_app", "close_app"} and settings.require_allowlist and settings.allowlist_hard_enforce:
            pname = ""
            if action == "launch_app":
                command = str(step.get("command", ""))
                pname = normalize_process_name(str(step.get("process_name_for_policy") or process_name_from_command(command)))
            else:
                name_filter = step.get("name_filter")
                pname = normalize_process_name(str(name_filter or ""))
            if pname and pname not in settings.allowlist:
                blocker = EnvelopeIssue(
                    code="allowlist_hard_block",
                    severity="high",
                    message=f"Process '{pname}' is not in allowlist and will be blocked.",
                    step_index=idx,
                    action=action,
                    hint="Add process to DESCON_ALLOWLIST or disable hard enforce.",
                )
                hard_blockers.append(blocker)
                issues.append(blocker)

        if action in {"click", "type", "hotkey", "key_press", "mouse_down", "mouse_up", "drag_to"}:
            warnings.append(
                EnvelopeIssue(
                    code="focus_sensitive_action",
                    severity="medium",
                    message=f"Action '{action}' is foreground-sensitive and may fail if focus changes.",
                    step_index=idx,
                    action=action,
                    hint="Precede with focus_window/focus_guard and keep guard enabled for mutating actions.",
                )
            )

        if action in {"click_element", "type_element", "element_action"} and not _coerce_bool(step.get("allow_fallback", True)):
            warnings.append(
                EnvelopeIssue(
                    code="uia_only_mode",
                    severity="medium",
                    message="Element action has fallback disabled; custom-rendered UI may fail.",
                    step_index=idx,
                    action=action,
                    hint="Enable allow_fallback and provide template_path/text_query fallback anchors.",
                )
            )

        if action == "read_text":
            backend = str(step.get("backend", "auto")).strip().lower()
            if backend == "host_model" and not settings.enable_host_ocr and not settings.best_effort_ocr:
                blocker = EnvelopeIssue(
                    code="host_ocr_disabled",
                    severity="high",
                    message="read_text backend=host_model requested, but host OCR is disabled and best_effort_ocr is off.",
                    step_index=idx,
                    action=action,
                    hint="Enable host OCR or switch backend to auto/local_model/tesseract.",
                )
                hard_blockers.append(blocker)
                issues.append(blocker)

    issues.extend(warnings)
    risk_score = min(100, len(hard_blockers) * 25 + len(warnings) * 8)
    return {
        "runtime_profile": runtime.policy.profile,
        "runtime_safe_mode": runtime.policy.safe_mode,
        "hard_blockers": [x.as_dict() for x in hard_blockers],
        "warnings": [x.as_dict() for x in warnings],
        "issues": [x.as_dict() for x in issues],
        "risk_score": risk_score,
        "ready": len(hard_blockers) == 0,
    }
