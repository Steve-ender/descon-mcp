from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

try:
    from scripts._bootstrap import ensure_project_root_on_path
except ModuleNotFoundError:
    from _bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from novaforge.config import load_settings
from novaforge.paths import artifacts_subdir
from novaforge.state import STATE
from novaforge.tools.orchestration_tools import execute_plan


RELIABILITY_SESSION_ID = "script_reliability_eval"


@dataclass
class Scenario:
    name: str
    plan: list[dict[str, Any]]
    expected_ok: bool = True
    runtime_profile: str = "basic_reliable"
    runtime_options: dict[str, Any] | None = None
    stop_on_error: bool = True


def _default_core_scenarios() -> list[Scenario]:
    return [
        Scenario(
            name="wait_short_success",
            plan=[{"action": "wait", "time_ms": 5}],
            expected_ok=True,
            runtime_profile="basic_reliable",
        ),
        Scenario(
            name="unknown_action_rejected",
            plan=[{"action": "unknown_action_xyz"}],
            expected_ok=False,
            runtime_profile="basic_reliable",
        ),
        Scenario(
            name="unknown_action_noop_when_enabled",
            plan=[{"action": "unknown_action_xyz"}],
            expected_ok=True,
            runtime_profile="balanced",
            runtime_options={"allow_unknown_actions": True},
        ),
        Scenario(
            name="safe_mode_confirm_enforced",
            plan=[{"action": "close_app", "name_filter": "notepad"}],
            expected_ok=False,
            runtime_profile="basic_reliable",
        ),
        Scenario(
            name="canary_screenshot_path_precheck",
            plan=[{"action": "screenshot", "path": "artifacts/reliability_eval/screens/default_probe.png"}],
            expected_ok=True,
            runtime_profile="basic_reliable",
            runtime_options={"canary_checks": True},
        ),
    ]


def _default_live_desktop_scenarios() -> list[Scenario]:
    note_path = artifacts_subdir("reliability_eval", "live_outputs") / "live_notepad_note.txt"
    now = datetime.now(timezone.utc).isoformat()
    return [
        Scenario(
            name="live_notepad_launch_type_close",
            plan=[
                {"action": "launch_app", "command": "notepad.exe", "process_name_for_policy": "notepad", "confirm": True},
                {"action": "wait", "time_ms": 800},
                {"action": "focus_window", "title_regex": ".*Notepad.*"},
                {"action": "type", "text": f"NovaForge live reliability eval at {now}"},
                {"action": "wait", "time_ms": 200},
                {"action": "close_app", "name_filter": "notepad", "confirm": True},
            ],
            expected_ok=True,
            runtime_profile="basic_reliable",
            runtime_options={"canary_checks": True, "auto_fallback": True, "fallback_policy": "conservative"},
        ),
        Scenario(
            name="live_notepad_save_flow",
            plan=[
                {"action": "launch_app", "command": "notepad.exe", "process_name_for_policy": "notepad", "confirm": True},
                {"action": "wait", "time_ms": 900},
                {"action": "focus_window", "title_regex": ".*Notepad.*"},
                {"action": "type", "text": f"Saved by NovaForge live reliability eval at {now}"},
                {"action": "hotkey", "keys": ["ctrl", "s"]},
                {"action": "wait", "time_ms": 700},
                {"action": "type", "text": str(note_path)},
                {"action": "key_press", "key": "enter"},
                {"action": "wait", "time_ms": 700},
                {"action": "close_app", "name_filter": "notepad", "confirm": True},
            ],
            expected_ok=True,
            runtime_profile="basic_reliable",
            runtime_options={"canary_checks": True, "auto_fallback": True, "fallback_policy": "aggressive"},
        ),
    ]


def _scenarios_from_file(path: Path) -> list[Scenario]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("scenarios") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError("Scenario file must be a list or an object with a 'scenarios' list")
    out: list[Scenario] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RuntimeError(f"Scenario at index {idx} must be an object")
        name = str(row.get("name") or f"scenario_{idx + 1}")
        plan = row.get("plan")
        if not isinstance(plan, list):
            raise RuntimeError(f"Scenario '{name}' missing list field: plan")
        out.append(
            Scenario(
                name=name,
                plan=plan,
                expected_ok=bool(row.get("expected_ok", True)),
                runtime_profile=str(row.get("runtime_profile", "basic_reliable")),
                runtime_options=row.get("runtime_options") if isinstance(row.get("runtime_options"), dict) else None,
                stop_on_error=bool(row.get("stop_on_error", True)),
            )
        )
    return out


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(int(v) for v in values)
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return int(ordered[idx])


def _mttr_attempts(series: list[bool]) -> dict[str, Any]:
    failures = [i for i, ok in enumerate(series) if not ok]
    if not failures:
        return {
            "mttr_attempts": 0.0,
            "recovered_failures": 0,
            "unrecovered_failures": 0,
            "recovery_attempts": [],
        }
    recovery_attempts: list[int] = []
    unrecovered = 0
    for i in failures:
        recovered_at = None
        for j in range(i + 1, len(series)):
            if series[j]:
                recovered_at = j
                break
        if recovered_at is None:
            unrecovered += 1
            continue
        recovery_attempts.append(recovered_at - i)
    mttr = (sum(recovery_attempts) / len(recovery_attempts)) if recovery_attempts else 0.0
    return {
        "mttr_attempts": round(mttr, 4),
        "recovered_failures": len(recovery_attempts),
        "unrecovered_failures": unrecovered,
        "recovery_attempts": recovery_attempts,
    }


def _max_consecutive_failures(series: list[bool]) -> int:
    longest = 0
    current = 0
    for ok in series:
        if ok:
            current = 0
            continue
        current += 1
        longest = max(longest, current)
    return longest


def _flakiness(series: list[bool]) -> float:
    if len(series) <= 1:
        return 0.0
    transitions = 0
    for i in range(1, len(series)):
        if bool(series[i]) != bool(series[i - 1]):
            transitions += 1
    return round(transitions / (len(series) - 1), 4)


async def _run_once(scenario: Scenario) -> dict[str, Any]:
    settings = load_settings()
    res = await execute_plan(
        plan=scenario.plan,
        settings=settings,
        stop_on_error=bool(scenario.stop_on_error),
        runtime_profile=scenario.runtime_profile,
        runtime_options=scenario.runtime_options,
        session_id=RELIABILITY_SESSION_ID,
    )
    data = res.get("data") if isinstance(res, dict) else {}
    err = res.get("error") if isinstance(res, dict) else {}
    return {
        "ok": bool(res.get("ok")) if isinstance(res, dict) else False,
        "timing_ms": int(res.get("timing_ms", 0)) if isinstance(res, dict) else 0,
        "error_code": err.get("code") if isinstance(err, dict) else None,
        "error_message": err.get("message") if isinstance(err, dict) else None,
        "step_count": int(data.get("count", 0)) if isinstance(data, dict) else 0,
    }


def _baseline_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("summary")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("scenario") or row.get("task") or "")
        if not name:
            continue
        out[name] = row
    return out


def _compare_baseline(current: list[dict[str, Any]], baseline_payload: dict[str, Any]) -> dict[str, Any]:
    baseline = _baseline_map(baseline_payload)
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    for row in current:
        name = str(row.get("scenario"))
        prev = baseline.get(name)
        if not prev:
            continue
        curr_pass = float(row.get("pass_rate", 0.0))
        prev_pass = float(prev.get("pass_rate", 0.0))
        curr_p95 = int(row.get("p95_timing_ms", 0))
        prev_p95 = int(prev.get("p95_timing_ms", 0))
        delta = {
            "scenario": name,
            "pass_rate_delta": round(curr_pass - prev_pass, 4),
            "p95_timing_ms_delta": curr_p95 - prev_p95,
        }
        if curr_pass + 1e-9 < prev_pass or curr_p95 > prev_p95:
            regressions.append(delta)
        elif curr_pass > prev_pass or curr_p95 < prev_p95:
            improvements.append(delta)
    return {
        "regressions": regressions,
        "improvements": improvements,
        "has_regression": bool(regressions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NovaForge reliability evaluation scenarios.")
    parser.add_argument("--repeats", type=int, default=20, help="Runs per scenario")
    parser.add_argument("--preset", choices=["core", "live_desktop"], default="core", help="Built-in scenario preset")
    parser.add_argument("--scenarios", type=str, default="", help="Optional JSON scenario file")
    parser.add_argument("--baseline", type=str, default="", help="Optional previous report for delta comparison")
    parser.add_argument("--seed-note", type=str, default="", help="Optional note field in output")
    args = parser.parse_args()

    repeats = max(1, int(args.repeats))
    if args.scenarios:
        scenarios = _scenarios_from_file(Path(args.scenarios))
    elif args.preset == "core":
        scenarios = _default_core_scenarios()
    elif args.preset == "live_desktop":
        scenarios = _default_live_desktop_scenarios()
    else:
        scenarios = _default_core_scenarios()

    out_dir = artifacts_subdir("reliability_eval")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out_path = out_dir / f"reliability_eval_{ts}.json"

    all_runs: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    overall_ok = True

    STATE.start(session_id=RELIABILITY_SESSION_ID)
    try:
        for scenario in scenarios:
            runs: list[dict[str, Any]] = []
            for i in range(repeats):
                run = asyncio.run(_run_once(scenario))
                run["run_index"] = i + 1
                run["expectation_ok"] = bool(run["ok"]) == bool(scenario.expected_ok)
                runs.append(run)
                all_runs.append({"scenario": scenario.name, **run})

            timings = [int(x.get("timing_ms", 0)) for x in runs]
            series = [bool(x.get("expectation_ok")) for x in runs]
            pass_rate = (sum(1 for x in series if x) / len(series)) if series else 0.0
            mttr = _mttr_attempts(series)
            error_codes: dict[str, int] = {}
            for run in runs:
                code = str(run.get("error_code") or "")
                if code:
                    error_codes[code] = error_codes.get(code, 0) + 1

            row = {
                "scenario": scenario.name,
                "expected_ok": scenario.expected_ok,
                "repeats": repeats,
                "pass_rate": round(pass_rate, 4),
                "median_timing_ms": int(median(timings)) if timings else 0,
                "p95_timing_ms": _p95(timings),
                "max_consecutive_failures": _max_consecutive_failures(series),
                "flakiness": _flakiness(series),
                "error_codes": error_codes,
                **mttr,
                "ok": pass_rate >= 0.95,
            }
            summary.append(row)
            overall_ok = overall_ok and bool(row["ok"])
    finally:
        STATE.stop(session_id=RELIABILITY_SESSION_ID)

    payload: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": overall_ok,
        "preset": args.preset,
        "repeats": repeats,
        "threshold_pass_rate": 0.95,
        "seed_note": args.seed_note,
        "summary": summary,
        "runs": all_runs,
    }

    if args.baseline:
        baseline_path = Path(args.baseline)
        if baseline_path.exists():
            baseline_payload = json.loads(baseline_path.read_text(encoding="utf-8"))
            payload["baseline_compare"] = _compare_baseline(summary, baseline_payload)
        else:
            payload["baseline_compare"] = {"error": f"Baseline not found: {baseline_path}"}

    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest = out_dir / "reliability_eval_latest.json"
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(str(out_path))
    print("OK" if overall_ok else "FAIL")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
