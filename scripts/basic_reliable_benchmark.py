from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from statistics import median

try:
    from scripts._bootstrap import ensure_project_root_on_path
except ModuleNotFoundError:
    from _bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from descon.config import load_settings
from descon.paths import artifacts_subdir
from descon.state import STATE
from descon.tools.orchestration_tools import execute_plan


BENCHMARK_SESSION_ID = "script_basic_reliable_benchmark"


TASKS: list[dict[str, object]] = [
    {
        "name": "wait_short_success",
        "plan": [{"action": "wait", "time_ms": 5}],
        "expected_ok": True,
        "expected_step_count": 1,
        "require_contract": True,
    },
    {
        "name": "unknown_action_rejected_in_basic_reliable",
        "plan": [{"action": "unknown_action_xyz"}],
        "runtime_profile": "basic_reliable",
        "expected_ok": False,
        "expected_error_codes": ["validation_error", "unsupported_action"],
    },
    {
        "name": "unknown_action_noop_allowed_balanced_override",
        "plan": [{"action": "unknown_action_xyz"}],
        "runtime_profile": "balanced",
        "runtime_options": {"allow_unknown_actions": True},
        "expected_ok": True,
        "expected_warning_contains": "no-op",
        "expected_step_count": 1,
        "require_contract": True,
    },
    {
        "name": "safe_mode_requires_confirm_negative_control",
        "plan": [{"action": "launch_app", "command": "notepad.exe", "process_name_for_policy": "notepad"}],
        "runtime_profile": "basic_reliable",
        "expected_ok": False,
        "expected_error_code": "runtime_error",
        "expected_error_message_contains": "confirm=true",
    },
    {
        "name": "safe_mode_with_confirm_compiles_to_success_shape",
        "plan": [
            {"action": "launch_app", "command": "notepad.exe", "process_name_for_policy": "notepad", "confirm": True},
            {"action": "wait", "time_ms": 100},
            {"action": "close_app", "name_filter": "notepad", "confirm": True},
        ],
        "runtime_profile": "basic_reliable",
        "expected_ok": True,
        "require_contract": True,
    },
]


def _validate_contract(res: dict[str, object]) -> list[str]:
    failures: list[str] = []
    data = res.get("data")
    if not isinstance(data, dict):
        return ["missing data payload"]
    steps = data.get("steps")
    if not isinstance(steps, list):
        return ["missing steps list"]
    required = {"name", "action", "attempt", "ok", "data", "error", "observation", "confirmation", "runtime_meta"}
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            failures.append(f"step[{idx}] is not an object")
            continue
        missing = sorted(required - set(step.keys()))
        if missing:
            failures.append(f"step[{idx}] missing keys: {','.join(missing)}")
        confirmation = step.get("confirmation")
        if not isinstance(confirmation, dict):
            failures.append(f"step[{idx}] missing confirmation object")
        else:
            for key in ("status", "reason", "evidence"):
                if key not in confirmation:
                    failures.append(f"step[{idx}] confirmation missing {key}")
    return failures


def _evaluate_expectations(task: dict[str, object], res: dict[str, object]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    expected_ok = bool(task.get("expected_ok", True))
    actual_ok = bool(res.get("ok"))
    if actual_ok != expected_ok:
        failures.append(f"expected ok={expected_ok}, got ok={actual_ok}")

    expected_error_code = task.get("expected_error_code")
    expected_error_codes = task.get("expected_error_codes")
    if expected_error_codes is not None:
        allowed_codes = [str(c) for c in expected_error_codes] if isinstance(expected_error_codes, list) else [str(expected_error_codes)]
        err_obj = res.get("error")
        actual_code = err_obj.get("code") if isinstance(err_obj, dict) else None
        if actual_code not in allowed_codes:
            failures.append(f"expected error_code in {allowed_codes}, got={actual_code}")
    elif expected_error_code is not None:
        err_obj = res.get("error")
        actual_code = err_obj.get("code") if isinstance(err_obj, dict) else None
        if actual_code != expected_error_code:
            failures.append(f"expected error_code={expected_error_code}, got={actual_code}")

    expected_error_message_contains = task.get("expected_error_message_contains")
    if expected_error_message_contains is not None:
        err_obj = res.get("error")
        actual_message = str(err_obj.get("message", "")) if isinstance(err_obj, dict) else ""
        if str(expected_error_message_contains).lower() not in actual_message.lower():
            failures.append(
                f"expected error message containing '{expected_error_message_contains}', got '{actual_message}'"
            )

    expected_warning_contains = task.get("expected_warning_contains")
    if expected_warning_contains is not None:
        steps = (res.get("data") or {}).get("steps") if isinstance(res.get("data"), dict) else None
        warning = ""
        if isinstance(steps, list) and steps and isinstance(steps[0], dict):
            warning = str((steps[0].get("data") or {}).get("warning", "")) if isinstance(steps[0].get("data"), dict) else ""
        if str(expected_warning_contains).lower() not in warning.lower():
            failures.append(f"expected warning containing '{expected_warning_contains}', got '{warning}'")

    expected_step_count = task.get("expected_step_count")
    if expected_step_count is not None:
        data = res.get("data")
        actual_count = int(data.get("count", 0)) if isinstance(data, dict) else 0
        if actual_count != int(expected_step_count):
            failures.append(f"expected step_count={expected_step_count}, got={actual_count}")

    if bool(task.get("require_contract", False)):
        failures.extend(_validate_contract(res))

    return (len(failures) == 0), failures


async def _run_once(task: dict[str, object]) -> dict[str, object]:
    settings = load_settings()
    profile = str(task.get("runtime_profile", "basic_reliable"))
    options = task.get("runtime_options")
    res = await execute_plan(
        plan=task["plan"],  # type: ignore[arg-type]
        settings=settings,
        stop_on_error=True,
        runtime_profile=profile,
        runtime_options=options if isinstance(options, dict) else None,
        session_id=BENCHMARK_SESSION_ID,
    )
    expectation_ok, expectation_failures = _evaluate_expectations(task=task, res=res if isinstance(res, dict) else {})
    return {
        "ok": bool(res.get("ok")),
        "timing_ms": int(res.get("timing_ms", 0)),
        "error_code": (res.get("error") or {}).get("code") if isinstance(res.get("error"), dict) else None,
        "step_count": int((res.get("data") or {}).get("count", 0)) if isinstance(res.get("data"), dict) else 0,
        "expectation_ok": expectation_ok,
        "expectation_failures": expectation_failures,
    }


def main() -> int:
    repeats = 10
    out_dir = artifacts_subdir("benchmarks")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out_path = out_dir / f"basic_reliable_benchmark_{ts}.json"

    all_runs: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    overall_pass = True

    STATE.start(session_id=BENCHMARK_SESSION_ID)
    try:
        for task in TASKS:
            task_name = str(task["name"])
            task_runs: list[dict[str, object]] = []
            for _ in range(repeats):
                run = asyncio.run(_run_once(task))
                task_runs.append(run)
                all_runs.append({"task": task_name, **run})
            pass_rate = sum(1 for r in task_runs if r["expectation_ok"]) / len(task_runs)
            timings = [int(r["timing_ms"]) for r in task_runs]
            steps = [int(r["step_count"]) for r in task_runs]
            errors: dict[str, int] = {}
            expectation_failure_count = 0
            for r in task_runs:
                c = str(r.get("error_code") or "")
                if c:
                    errors[c] = errors.get(c, 0) + 1
                if not bool(r.get("expectation_ok")):
                    expectation_failure_count += 1
            ok_task = pass_rate >= 0.95
            overall_pass = overall_pass and ok_task
            summary_rows.append(
                {
                    "task": task_name,
                    "expected_ok": bool(task.get("expected_ok", True)),
                    "repeats": repeats,
                    "pass_rate": round(pass_rate, 4),
                    "expectation_failures": expectation_failure_count,
                    "median_timing_ms": median(timings) if timings else 0,
                    "median_step_count": median(steps) if steps else 0,
                    "error_codes": errors,
                    "ok": ok_task,
                }
            )
    finally:
        STATE.stop(session_id=BENCHMARK_SESSION_ID)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": overall_pass,
        "threshold_pass_rate": 0.95,
        "expectation_mode": True,
        "summary": summary_rows,
        "runs": all_runs,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(str(out_path))
    print("OK" if overall_pass else "FAIL")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
