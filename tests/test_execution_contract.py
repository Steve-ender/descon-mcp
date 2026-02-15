from __future__ import annotations

import asyncio

from descon.config import load_settings
from descon.state import STATE
import descon.tools.orchestration_tools as orchestration_tools


def test_desktop_act_step_contract_shape():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "wait", "time_ms": 1}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
            )
        )
        assert res["ok"] is True
        runtime = res["data"]["runtime"]
        assert "profile" in runtime
        assert "state_handoff" in runtime
        assert "transcript" in runtime

        step = res["data"]["steps"][0]
        assert "name" in step
        assert "action" in step
        assert "attempt" in step
        assert "ok" in step
        assert "data" in step
        assert "error" in step
        assert "observation" in step
        assert "confirmation" in step
        assert "runtime_meta" in step
        assert "status" in step["confirmation"]
        assert "reason" in step["confirmation"]
        assert "evidence" in step["confirmation"]
    finally:
        STATE.stop()
