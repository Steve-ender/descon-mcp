from __future__ import annotations

import asyncio

from descon.config import load_settings
from descon.state import STATE
import descon.tools.orchestration_tools as orchestration_tools


def test_basic_reliable_rejects_unknown_action():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "unknown_action_xyz"}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
            )
        )
        assert res["ok"] is False
        assert res["error"]["code"] in {"unsupported_action", "runtime_error", "validation_error"}
    finally:
        STATE.stop()


def test_balanced_allows_unknown_action_when_override_enabled():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "unknown_action_xyz"}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="balanced",
                runtime_options={"allow_unknown_actions": True},
            )
        )
        assert res["ok"] is True
        step = res["data"]["steps"][0]
        assert "no-op" in step["data"]["warning"].lower()
    finally:
        STATE.stop()
