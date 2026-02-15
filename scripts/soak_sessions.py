from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass
class SoakTask:
    name: str
    plan: list[dict[str, Any]]
    expected_ok: bool = True
    runtime_profile: str = "basic_reliable"
    runtime_options: dict[str, Any] | None = None


def _default_tasks(preset: str) -> list[SoakTask]:
    p = preset.strip().lower()
    if p == "core":
        return [
            SoakTask(
                name="wait",
                plan=[{"action": "wait", "time_ms": 5}],
                expected_ok=True,
            ),
            SoakTask(
                name="unknown_rejected",
                plan=[{"action": "unknown_action_xyz"}],
                expected_ok=False,
            ),
            SoakTask(
                name="unknown_noop_balanced",
                plan=[{"action": "unknown_action_xyz"}],
                expected_ok=True,
                runtime_profile="balanced",
                runtime_options={"allow_unknown_actions": True},
            ),
        ]
    return [SoakTask(name="wait", plan=[{"action": "wait", "time_ms": 5}], expected_ok=True)]


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return int(ordered[idx])


async def _run_one(task: SoakTask, session_id: str) -> dict[str, Any]:
    settings = load_settings()
    res = await execute_plan(
        plan=task.plan,
        settings=settings,
        stop_on_error=True,
        runtime_profile=task.runtime_profile,
        runtime_options=task.runtime_options,
        session_id=session_id,
    )
    err = res.get("error") if isinstance(res, dict) else {}
    return {
        "ok": bool(res.get("ok")) if isinstance(res, dict) else False,
        "timing_ms": int(res.get("timing_ms", 0)) if isinstance(res, dict) else 0,
        "error_code": err.get("code") if isinstance(err, dict) else None,
    }


async def _worker_loop(
    worker_id: int,
    session_id: str,
    tasks: list[SoakTask],
    deadline_monotonic: float,
) -> dict[str, Any]:
    STATE.start(session_id=session_id)
    runs: list[dict[str, Any]] = []
    try:
        idx = 0
        while time.monotonic() < deadline_monotonic:
            task = tasks[idx % len(tasks)]
            idx += 1
            run = await _run_one(task, session_id=session_id)
            run["task"] = task.name
            run["expected_ok"] = task.expected_ok
            run["expectation_ok"] = bool(run["ok"]) == bool(task.expected_ok)
            runs.append(run)
            # Force cooperative scheduling even if plan execution completed without yielding.
            await asyncio.sleep(0)
    finally:
        STATE.stop(session_id=session_id)

    pass_series = [bool(r["expectation_ok"]) for r in runs]
    pass_rate = (sum(1 for x in pass_series if x) / len(pass_series)) if pass_series else 0.0
    timings = [int(r.get("timing_ms", 0)) for r in runs]
    err_codes: dict[str, int] = {}
    for row in runs:
        code = str(row.get("error_code") or "")
        if code:
            err_codes[code] = err_codes.get(code, 0) + 1

    return {
        "worker_id": worker_id,
        "session_id": session_id,
        "runs": len(runs),
        "pass_rate": round(pass_rate, 4),
        "median_timing_ms": int(median(timings)) if timings else 0,
        "p95_timing_ms": _p95(timings),
        "error_codes": err_codes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run multi-session soak for NovaForge orchestration stability.")
    parser.add_argument("--duration-seconds", type=int, default=120, help="Total soak duration in seconds")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent worker sessions")
    parser.add_argument("--preset", choices=["core"], default="core", help="Task preset")
    parser.add_argument("--min-pass-rate", type=float, default=0.99, help="Required aggregate expectation pass rate")
    parser.add_argument("--min-runs-per-worker", type=int, default=1, help="Minimum executions required per worker")
    args = parser.parse_args()

    duration_s = max(10, int(args.duration_seconds))
    workers = max(1, int(args.workers))
    min_pass_rate = max(0.0, min(1.0, float(args.min_pass_rate)))
    min_runs_per_worker = max(1, int(args.min_runs_per_worker))
    tasks = _default_tasks(args.preset)

    deadline = time.monotonic() + duration_s
    async def _run_workers() -> list[dict[str, Any]]:
        return await asyncio.gather(
            *[
                _worker_loop(
                    worker_id=i + 1,
                    session_id=f"script_soak_worker_{i + 1}",
                    tasks=tasks,
                    deadline_monotonic=deadline,
                )
                for i in range(workers)
            ]
        )

    per_worker = asyncio.run(_run_workers())

    total_runs = sum(int(row.get("runs", 0)) for row in per_worker)
    weighted_pass_numer = sum(float(row.get("pass_rate", 0.0)) * int(row.get("runs", 0)) for row in per_worker)
    aggregate_pass_rate = (weighted_pass_numer / total_runs) if total_runs else 0.0
    worker_min_runs_ok = all(int(row.get("runs", 0)) >= min_runs_per_worker for row in per_worker)
    all_ok = (aggregate_pass_rate >= min_pass_rate) and worker_min_runs_ok

    out_dir = artifacts_subdir("soak")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out_path = out_dir / f"soak_sessions_{ts}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": all_ok,
        "duration_seconds": duration_s,
        "workers": workers,
        "preset": args.preset,
        "min_pass_rate": min_pass_rate,
        "min_runs_per_worker": min_runs_per_worker,
        "worker_min_runs_ok": worker_min_runs_ok,
        "aggregate_pass_rate": round(aggregate_pass_rate, 6),
        "total_runs": total_runs,
        "workers_summary": per_worker,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest = out_dir / "soak_sessions_latest.json"
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(str(out_path))
    print("OK" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
