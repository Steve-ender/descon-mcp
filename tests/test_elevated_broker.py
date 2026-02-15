from __future__ import annotations

import asyncio
import sys
import time

import pytest

from descon.config import load_settings
from descon.elevated_broker import ElevatedBrokerClient
from descon.state import STATE
import descon.tools.orchestration_tools as orchestration_tools
from descon.elevated_broker import ELEVATED_BROKER


def test_broker_should_route_force_broker_when_enabled(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "click", "force_broker": True},
        action="click",
        runtime_options=None,
    )
    assert route is True
    assert reason == "force_broker"


def test_broker_should_not_route_when_disabled(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "0")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "click"},
        action="click",
        runtime_options=None,
    )
    assert route is False
    assert reason == "broker_disabled_policy"


def test_broker_should_not_route_non_routable_action(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "wait"},
        action="wait",
        runtime_options=None,
    )
    assert route is False
    assert reason == "not_routable_action"


def test_broker_mode_never_disables_routing(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "click"},
        action="click",
        runtime_options={"broker_mode": "never"},
    )
    assert route is False
    assert reason == "broker_mode_never"


def test_broker_mode_always_routes_routable_actions(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "click"},
        action="click",
        runtime_options={"broker_mode": "always"},
    )
    assert route is True
    assert reason == "broker_mode_always"


def test_broker_mode_always_routes_non_routable_actions_too(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "wait"},
        action="wait",
        runtime_options={"broker_mode": "always"},
    )
    assert route is True
    assert reason == "broker_mode_always"


def test_broker_mode_always_routes_even_when_disabled_to_fail_fast(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "0")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "click"},
        action="click",
        runtime_options={"broker_mode": "always"},
    )
    assert route is True
    assert reason == "broker_mode_always_disabled"


def test_broker_disabled_option_string_false_does_not_disable(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "click"},
        action="click",
        runtime_options={"broker_disabled": "false", "broker_mode": "always"},
    )
    assert route is True
    assert reason == "broker_mode_always"


def test_broker_conflicting_route_directives_are_detected(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "click", "force_local": True, "force_broker": True},
        action="click",
        runtime_options=None,
    )
    assert route is False
    assert reason == "conflicting_route_directives"


def test_broker_string_false_route_directives_are_not_treated_as_true(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    route, reason = ELEVATED_BROKER.should_route_step(
        settings=settings,
        step={"action": "click", "force_local": "false", "force_broker": "false"},
        action="click",
        runtime_options={"broker_mode": "always"},
    )
    assert route is True
    assert reason == "broker_mode_always"


def test_orchestration_broker_fallback_string_false_does_not_fallback(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "should_route_step",
            lambda **kwargs: (True, "force_broker"),
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "execute_step",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
        )
        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "click", "x": 1, "y": 1, "force_broker": True}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
                runtime_options={"broker_fallback_local": "false"},
            )
            assert res["ok"] is False
            assert res["error"]["code"] in {"broker_unavailable", "runtime_error"}
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_orchestration_routes_step_via_broker_when_forced(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")

        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "should_route_step",
            lambda **kwargs: (True, "force_broker"),
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "execute_step",
            lambda **kwargs: {
                "step": {
                    "name": "step_1",
                    "action": "wait",
                    "attempt": 1,
                    "ok": True,
                    "data": {"slept_ms": 1, "strategy": "broker"},
                    "error": None,
                    "observation": {"pre": None, "post": None},
                    "confirmation": {"status": "confirmed", "reason": "broker", "evidence": {}},
                    "runtime_meta": {"profile": "basic_reliable", "strategy_path": ["wait", "broker"]},
                }
            },
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "status",
            lambda **kwargs: {"running": True, "pid": 12345, "configured": True},
        )

        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "wait", "time_ms": 1, "force_broker": True}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
            )
            assert res["ok"] is True
            step = res["data"]["steps"][0]
            assert step["ok"] is True
            assert step["data"]["broker"]["strategy"] == "broker"
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_broker_take_response_keeps_unmatched_messages():
    client = ElevatedBrokerClient()
    client._queue.put({"id": "other-1", "ok": True, "data": {"x": 1}})
    client._queue.put({"id": "target-1", "ok": True, "data": {"y": 2}})
    out = client._take_response(req_id="target-1", timeout_s=0.1)
    assert out["id"] == "target-1"
    assert "other-1" in client._pending


def test_broker_default_command_uses_current_python(monkeypatch):
    monkeypatch.delenv("DESCON_ELEVATED_BROKER_COMMAND", raising=False)
    settings = load_settings()
    client = ElevatedBrokerClient()
    cmd = client._command(settings)
    assert cmd[0] == sys.executable


def test_orchestration_broker_route_skips_local_foreground_guard(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
        monkeypatch.setattr(
            orchestration_tools,
            "_guard_foreground_for_step",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("local guard should not run")),
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "should_route_step",
            lambda **kwargs: (True, "force_broker"),
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "execute_step",
            lambda **kwargs: {
                "step": {
                    "name": "step_1",
                    "action": "click",
                    "attempt": 1,
                    "ok": True,
                    "data": {"strategy": "broker"},
                    "error": None,
                    "observation": {"pre": None, "post": None},
                    "confirmation": {"status": "confirmed", "reason": "broker", "evidence": {}},
                    "runtime_meta": {"profile": "basic_reliable", "strategy_path": ["click", "broker"]},
                }
            },
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "status",
            lambda **kwargs: {"running": True, "pid": 2222, "configured": True},
        )
        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "click", "x": 10, "y": 10, "force_broker": True}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
            )
            assert res["ok"] is True
            assert res["data"]["steps"][0]["data"]["broker"]["route_reason"] == "force_broker"
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_orchestration_noncanonical_broker_step_falls_back_local_when_enabled(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "should_route_step",
            lambda **kwargs: (True, "force_broker"),
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "execute_step",
            lambda **kwargs: {"step": {"unexpected": True}},
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "status",
            lambda **kwargs: {"running": True, "pid": 3333, "configured": True},
        )
        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "click", "x": 1, "y": 1, "force_broker": True}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
                runtime_options={"broker_fallback_local": True},
            )
            assert res["ok"] is True
            step = res["data"]["steps"][0]
            assert step["ok"] is True
            assert step["data"]["broker_route_note"]["fallback_local"] is True
            assert "broker_fallback_local" in step["runtime_meta"]["strategy_path"]
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_orchestration_broker_step_ok_false_fails_without_local_fallback(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "should_route_step",
            lambda **kwargs: (True, "force_broker"),
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "execute_step",
            lambda **kwargs: {
                "step": {
                    "name": "bad",
                    "action": "click",
                    "attempt": 1,
                    "ok": False,
                    "data": {},
                    "error": {"code": "x", "message": "bad"},
                    "observation": {"pre": None, "post": None},
                    "confirmation": {"status": "failed", "reason": "action_failed", "evidence": {}},
                    "runtime_meta": {"profile": "basic_reliable", "strategy_path": ["click", "broker"]},
                }
            },
        )
        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "click", "x": 1, "y": 1, "force_broker": True}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
            )
            assert res["ok"] is False
            assert res["error"]["code"] in {"broker_unavailable", "runtime_error"}
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_orchestration_normalizes_broker_runtime_meta(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "should_route_step",
            lambda **kwargs: (True, "broker_mode_always"),
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "execute_step",
            lambda **kwargs: {
                "step": {
                    "name": "step_x",
                    "action": "click",
                    "attempt": 1,
                    "ok": True,
                    "data": {"done": True},
                    "error": None,
                    "observation": {"pre": None, "post": None},
                    "confirmation": {"status": "confirmed", "reason": "broker", "evidence": {}},
                    "runtime_meta": {"profile": "strict", "strategy_path": ["click"]},
                }
            },
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "status",
            lambda **kwargs: {"running": True, "pid": 4444, "configured": True},
        )
        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "click", "x": 1, "y": 1}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
                runtime_options={"broker_mode": "always"},
            )
            assert res["ok"] is True
            step = res["data"]["steps"][0]
            assert step["runtime_meta"]["profile"] == "basic_reliable"
            assert "broker" in step["runtime_meta"]["strategy_path"]
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_orchestration_broker_fallback_local_on_error(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "should_route_step",
            lambda **kwargs: (True, "broker_mode_always"),
        )
        monkeypatch.setattr(
            orchestration_tools.ELEVATED_BROKER,
            "execute_step",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
        )
        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "wait", "time_ms": 1}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
                runtime_options={"broker_mode": "always", "broker_fallback_local": True},
            )
            assert res["ok"] is True
            step = res["data"]["steps"][0]
            assert "broker_fallback_local" in step["runtime_meta"]["strategy_path"]
            assert step["data"]["broker_route_note"]["fallback_local"] is True
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_orchestration_broker_mode_always_disabled_fails_fast(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "0")
        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "click", "x": 1, "y": 1}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
                runtime_options={"broker_mode": "always"},
            )
            assert res["ok"] is False
            assert res["error"]["code"] in {"broker_unavailable", "runtime_error"}
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_orchestration_conflicting_route_directives_fail_validation(monkeypatch):
    async def _run():
        monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
        STATE.start()
        try:
            res = await orchestration_tools.execute_plan(
                plan=[{"action": "click", "x": 1, "y": 1, "force_local": True, "force_broker": True}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
                runtime_options={"allow_unknown_actions": True},
            )
            assert res["ok"] is False
            assert res["error"]["code"] == "validation_error"
        finally:
            STATE.stop()

    asyncio.run(_run())


def test_broker_status_contains_failure_state(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    client = ElevatedBrokerClient()
    client._record_failure("x")
    s = client.status(settings)
    assert s["configured"] is True
    assert s["configuration_source"] in {"default", "explicit"}
    assert "consecutive_failures" in s
    assert "cooldown_until_ms" in s


def test_broker_status_marks_default_configuration_source(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    monkeypatch.delenv("DESCON_ELEVATED_BROKER_COMMAND", raising=False)
    settings = load_settings()
    client = ElevatedBrokerClient()
    s = client.status(settings)
    assert s["configured"] is True
    assert s["configuration_source"] == "default"


def test_broker_cooldown_failure_does_not_extend(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    client = ElevatedBrokerClient()
    base = 100000
    client._cooldown_until_ms = base + 3000
    client._consecutive_failures = 3
    client._record_failure("Elevated broker in cooldown after repeated failures", now_ms=base + 1000)
    assert client._cooldown_until_ms == base + 3000
    assert client._consecutive_failures == 3


def test_broker_request_retries_once_on_recoverable_failure(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    client = ElevatedBrokerClient()

    calls = {"n": 0}

    class _Stdin:
        def write(self, _s):  # noqa: D401
            return None

        def flush(self):
            return None

    def fake_start(_settings):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Elevated broker process is not available")
        client._proc = type("P", (), {"stdin": _Stdin(), "poll": lambda self: None, "pid": 1234})()

    monkeypatch.setattr(client, "_start_if_needed", fake_start)
    monkeypatch.setattr(client, "_take_response", lambda **kwargs: {"id": "x", "ok": True, "data": {}})
    monkeypatch.setattr(client, "_shutdown_process", lambda preserve_failure_state: None)

    client._proc = type("Proc", (), {"stdin": _Stdin(), "poll": lambda self: None, "pid": 1})()
    out = client._request(settings=settings, op="ping", payload={})
    assert out["ok"] is True
    assert calls["n"] == 2


def test_broker_request_retries_once_on_timeout_failure(monkeypatch):
    monkeypatch.setenv("DESCON_ENABLE_ELEVATED_BROKER", "1")
    settings = load_settings()
    client = ElevatedBrokerClient()

    calls = {"start": 0, "take": 0}

    class _Stdin:
        def write(self, _s):
            return None

        def flush(self):
            return None

    def fake_start(_settings):
        calls["start"] += 1
        client._proc = type("P", (), {"stdin": _Stdin(), "poll": lambda self: None, "pid": 5678})()

    def fake_take(**kwargs):
        calls["take"] += 1
        if calls["take"] == 1:
            raise RuntimeError("Timed out waiting for elevated broker response")
        return {"id": "x", "ok": True, "data": {}}

    monkeypatch.setattr(client, "_start_if_needed", fake_start)
    monkeypatch.setattr(client, "_take_response", fake_take)
    monkeypatch.setattr(client, "_shutdown_process", lambda preserve_failure_state: None)

    out = client._request(settings=settings, op="ping", payload={})
    assert out["ok"] is True
    assert calls["start"] == 2
    assert calls["take"] == 2


def test_broker_shutdown_preserve_failure_state():
    client = ElevatedBrokerClient()
    client._consecutive_failures = 4
    client._last_error = "x"
    client._last_failure_ms = 123
    client._cooldown_until_ms = 999
    client._shutdown_process(preserve_failure_state=True)
    assert client._consecutive_failures == 4
    assert client._last_error == "x"
    assert client._last_failure_ms == 123
    assert client._cooldown_until_ms == 999


def test_take_response_times_out_with_unmatched_messages():
    client = ElevatedBrokerClient()
    for i in range(50):
        client._queue.put({"id": f"other-{i}", "ok": True, "data": {}})
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="Timed out waiting for elevated broker response"):
        client._take_response(req_id="target-never", timeout_s=0.05)
    elapsed = time.monotonic() - started
    assert elapsed < 0.5


def test_take_response_pending_overflow_evicts_oldest_not_all():
    client = ElevatedBrokerClient()
    client._pending_limit = 2
    client._queue.put({"id": "other-1", "ok": True, "data": {}})
    client._queue.put({"id": "other-2", "ok": True, "data": {}})
    client._queue.put({"id": "other-3", "ok": True, "data": {}})
    with pytest.raises(RuntimeError, match="Timed out waiting for elevated broker response"):
        client._take_response(req_id="target-never", timeout_s=0.02)
    assert len(client._pending) == 2
    assert "other-1" not in client._pending
    assert "other-2" in client._pending
    assert "other-3" in client._pending
