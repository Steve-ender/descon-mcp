from __future__ import annotations

import asyncio
import json

try:
    from scripts._bootstrap import ensure_project_root_on_path
except ModuleNotFoundError:
    from _bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from descon.config import load_settings
from descon.engines.activity_indicator import ACTIVITY_INDICATOR
from descon.paths import artifacts_subdir
from descon.engines.screen_engine import SCREEN_ENGINE
from descon.engines.window_engine import WINDOW_ENGINE
from descon.state import STATE
from descon.tools.orchestration_tools import execute_plan


SMOKE_SESSION_ID = "script_production_smoke"


def main() -> int:
    out_dir = artifacts_subdir("smoke")
    report_path = out_dir / "production_smoke_report.json"

    report: dict[str, object] = {"ok": False, "checks": []}
    checks: list[dict[str, object]] = []

    def add_check(name: str, ok: bool, detail: object) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        settings = load_settings()
        add_check(
            "config_load",
            True,
            {
                "require_allowlist": settings.require_allowlist,
                "allowlist_size": len(settings.allowlist),
                "max_actions": settings.max_actions,
            },
        )

        STATE.start(session_id=SMOKE_SESSION_ID)
        st = STATE.get(session_id=SMOKE_SESSION_ID)
        add_check("session_start", st.active, {"active": st.active, "session_id": SMOKE_SESSION_ID})

        fg = WINDOW_ENGINE.get_foreground_window()
        add_check("foreground_probe", bool(fg.get("handle")), fg)

        if fg.get("handle"):
            guard = WINDOW_ENGINE.ensure_foreground_target(
                expected_handle=int(fg["handle"]),
                mismatch_mode="warn",
                retries=1,
            )
            add_check("foreground_guard", bool(guard.get("ok")), guard)
        else:
            add_check("foreground_guard", False, "No foreground window handle")

        windows = WINDOW_ENGINE.list_windows(only_visible=True)
        add_check("window_list", len(windows) > 0, {"count": len(windows)})

        # Monitor index validation
        mon_count = len(SCREEN_ENGINE.monitors())
        try:
            SCREEN_ENGINE.capture(path=None, monitor_index=max(0, mon_count - 1), region=None)
            add_check("screen_monitor_capture_valid", True, {"monitor_count": mon_count})
        except Exception as e:
            add_check("screen_monitor_capture_valid", False, str(e))

        async def _run_plan() -> dict:
            return await execute_plan(
                plan=[{"action": "wait", "time_ms": 10}],
                settings=settings,
                stop_on_error=True,
                session_id=SMOKE_SESSION_ID,
            )

        plan_res = asyncio.run(_run_plan())
        add_check("execute_plan_wait", bool(plan_res.get("ok")), plan_res)

        glow_status = ACTIVITY_INDICATOR.status()
        add_check("activity_indicator_status", True, glow_status)

        # Session reuse semantics
        reused = STATE.get(session_id=SMOKE_SESSION_ID)
        add_check("session_reuse_semantics", reused.active, {"active": reused.active, "action_count": reused.action_count})
    except Exception as e:
        add_check("exception", False, str(e))
    finally:
        try:
            STATE.stop(session_id=SMOKE_SESSION_ID)
        except Exception:
            pass

    report["checks"] = checks
    report["ok"] = all(bool(c.get("ok")) for c in checks)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(str(report_path))
    print("OK" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
