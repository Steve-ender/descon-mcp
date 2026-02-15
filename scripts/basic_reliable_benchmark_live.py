from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
import psutil

try:
    from scripts._bootstrap import ensure_project_root_on_path
except ModuleNotFoundError:
    from _bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from novaforge.config import load_settings
from novaforge.paths import artifacts_subdir
from novaforge.state import STATE
from novaforge.tools.orchestration_tools import execute_plan
from novaforge.engines.window_engine import WINDOW_ENGINE


LIVE_BENCHMARK_SESSION_ID = "script_basic_reliable_benchmark_live"


def _live_note_path() -> Path:
    out = artifacts_subdir("benchmarks", "live_outputs")
    return out / "live_notepad_note.txt"


def _tasks(note_path: Path) -> list[dict[str, object]]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": "notepad_launch_focus_type_close",
            "runtime_profile": "basic_reliable",
            "plan": [
                {"action": "launch_app", "command": "notepad.exe", "process_name_for_policy": "notepad", "confirm": True},
                {"action": "wait", "time_ms": 900},
                {"action": "focus_window", "title_regex": ".*Notepad.*"},
                {"action": "type", "text": f"NovaForge live benchmark run at {now}"},
                {"action": "wait", "time_ms": 300},
                {"action": "screenshot"},
            ],
        },
        {
            "name": "notepad_save_file_flow",
            "runtime_profile": "basic_reliable",
            "plan": [
                {"action": "launch_app", "command": "notepad.exe", "process_name_for_policy": "notepad", "confirm": True},
                {"action": "wait", "time_ms": 900},
                {"action": "focus_window", "title_regex": ".*Notepad.*"},
                {"action": "type", "text": f"Saved by NovaForge basic_reliable benchmark at {now}"},
                {"action": "hotkey", "keys": ["ctrl", "s"]},
                {"action": "wait", "time_ms": 900},
                {"action": "type", "text": str(note_path)},
                {"action": "key_press", "key": "enter"},
                {"action": "wait", "time_ms": 900},
                {"action": "screenshot"},
            ],
        },
        {
            "name": "mouse_primitives_drag_sequence",
            "runtime_profile": "basic_reliable",
            "plan": [
                {"action": "move_mouse", "x": 220, "y": 220, "duration_ms": 100},
                {"action": "mouse_down", "x": 220, "y": 220},
                {"action": "drag_to", "x": 360, "y": 280, "duration_ms": 180},
                {"action": "mouse_up", "x": 360, "y": 280},
                {"action": "wait", "time_ms": 200},
                {"action": "screenshot"},
            ],
        },
    ]


def _notepad_pids() -> set[int]:
    out: set[int] = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = str(proc.info.get("name") or "").lower()
            if name in {"notepad.exe", "notepad"}:
                out.add(int(proc.info["pid"]))
        except Exception:
            continue
    return out


def _launched_pids_from_result(res: dict[str, object]) -> set[int]:
    out: set[int] = set()
    data = res.get("data")
    if not isinstance(data, dict):
        return out
    steps = data.get("steps")
    if not isinstance(steps, list):
        return out
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("action")) != "launch_app":
            continue
        sdata = step.get("data")
        if not isinstance(sdata, dict):
            continue
        pid = sdata.get("pid")
        try:
            if pid is not None:
                out.add(int(pid))
        except Exception:
            pass
    return out


def _cleanup_pids(pids: set[int]) -> list[int]:
    closed: list[int] = []
    for pid in sorted(pids):
        try:
            WINDOW_ENGINE.close_app(pid=pid)
            closed.append(pid)
        except Exception:
            continue
    return closed


async def _run_once(task: dict[str, object]) -> dict[str, object]:
    settings = load_settings()
    baseline = _notepad_pids()
    res = await execute_plan(
        plan=task["plan"],  # type: ignore[arg-type]
        settings=settings,
        stop_on_error=True,
        runtime_profile=str(task.get("runtime_profile", "basic_reliable")),
        session_id=LIVE_BENCHMARK_SESSION_ID,
    )
    after = _notepad_pids()
    launched = _launched_pids_from_result(res if isinstance(res, dict) else {})
    cleaned = _cleanup_pids(launched)
    residual_new = sorted((after - baseline) - set(cleaned))
    data = res.get("data", {}) if isinstance(res, dict) else {}
    error = res.get("error") if isinstance(res, dict) else None
    return {
        "ok": bool(res.get("ok")) if isinstance(res, dict) else False,
        "timing_ms": int(res.get("timing_ms", 0)) if isinstance(res, dict) else 0,
        "error_code": error.get("code") if isinstance(error, dict) else None,
        "error_message": error.get("message") if isinstance(error, dict) else None,
        "step_count": int(data.get("count", 0)) if isinstance(data, dict) else 0,
        "launch_owned_notepad_pids": sorted(launched),
        "new_notepad_pids_before_cleanup": sorted(after - baseline),
        "residual_new_notepad_pids": residual_new,
        "cleaned_notepad_pids": cleaned,
        "cleanup_scope": "launch_owned_only",
    }


def main() -> int:
    repeats = 3
    note_path = _live_note_path()
    bench_dir = artifacts_subdir("benchmarks")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out_path = bench_dir / f"basic_reliable_live_benchmark_{ts}.json"

    tasks = _tasks(note_path)
    all_runs: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    overall_ok = True

    STATE.start(session_id=LIVE_BENCHMARK_SESSION_ID)
    try:
        for task in tasks:
            name = str(task["name"])
            runs: list[dict[str, object]] = []
            for _ in range(repeats):
                run = asyncio.run(_run_once(task))
                runs.append(run)
                all_runs.append({"task": name, **run})
            pass_rate = sum(1 for r in runs if r["ok"]) / len(runs)
            timings = [int(r["timing_ms"]) for r in runs]
            steps = [int(r["step_count"]) for r in runs]
            codes: dict[str, int] = {}
            for r in runs:
                c = str(r.get("error_code") or "")
                if c:
                    codes[c] = codes.get(c, 0) + 1
            task_ok = pass_rate >= 0.67
            overall_ok = overall_ok and task_ok
            summary_rows.append(
                {
                    "task": name,
                    "repeats": repeats,
                    "pass_rate": round(pass_rate, 4),
                    "median_timing_ms": median(timings) if timings else 0,
                    "median_step_count": median(steps) if steps else 0,
                    "error_codes": codes,
                    "ok": task_ok,
                }
            )
    finally:
        STATE.stop(session_id=LIVE_BENCHMARK_SESSION_ID)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": overall_ok,
        "threshold_pass_rate": 0.67,
        "live_note_target": str(note_path),
        "note_exists_after_run": note_path.exists(),
        "summary": summary_rows,
        "runs": all_runs,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(str(out_path))
    print("OK" if overall_ok else "FAIL")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
