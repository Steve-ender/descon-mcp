from __future__ import annotations

import asyncio

from descon.config import load_settings
from descon.state import STATE
import descon.tools.orchestration_tools as orchestration_tools


def _assert_canonical_step_shape(step: dict):
    for key in ("name", "action", "attempt", "ok", "data", "error", "observation", "confirmation", "runtime_meta"):
        assert key in step


def test_stop_on_error_false_missing_action_returns_canonical_step():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"name": "bad_step_missing_action"}],
                settings=load_settings(),
                stop_on_error=False,
                runtime_profile="balanced",
                runtime_options={"allow_unknown_actions": True},
            )
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "partial_failure"
        steps = res["data"]["steps"]
        assert len(steps) == 1
        step = steps[0]
        _assert_canonical_step_shape(step)
        assert step["ok"] is False
        assert step["error"]["code"] == "validation_error"
        assert len(res["data"]["runtime"]["transcript"]) == 1
    finally:
        STATE.stop()


def test_stop_on_error_false_runtime_failure_returns_canonical_step():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "unknown_action_xyz"}],
                settings=load_settings(),
                stop_on_error=False,
                runtime_profile="balanced",
                runtime_options={"allow_unknown_actions": False},
            )
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "validation_error"
        # Schema validation failed before execution loop, so no per-step payload is returned.
        assert "data" not in res

        # Use a runtime-level failing step to verify canonical failure shape in non-stop mode.
        res2 = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "focus_guard", "window_handle": 999999, "mismatch_mode": "fail"}],
                settings=load_settings(),
                stop_on_error=False,
                runtime_profile="balanced",
                runtime_options={"allow_unknown_actions": True},
            )
        )
        assert res2["ok"] is False
        assert res2["error"]["code"] == "partial_failure"
        steps2 = res2["data"]["steps"]
        assert len(steps2) == 1
        step = steps2[0]
        _assert_canonical_step_shape(step)
        assert step["ok"] is False
        assert step["error"]["code"] in {"foreground_refocus_failed", "foreground_mismatch", "not_found", "runtime_error"}
        assert len(res2["data"]["runtime"]["transcript"]) == 1
    finally:
        STATE.stop()
