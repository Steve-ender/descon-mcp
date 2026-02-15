from __future__ import annotations

import asyncio

from novaforge.config import load_settings
from novaforge.state import STATE
import novaforge.tools.orchestration_tools as orchestration_tools
import novaforge.tools.wait_tools as wait_tools
from novaforge.safety import assert_can_run, assert_process_allowed


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


class _FakeSampleResult:
    def __init__(self, text: str):
        self.text = text


class _FakeCtx:
    async def sample(self, **kwargs):
        return _FakeSampleResult("hello from host ocr")


def test_autostart_session_allows_tool_call_without_manual_start(monkeypatch):
    fake = _FakeMCP()
    wait_tools.register_wait_tools(fake)
    fn = fake.tools["desktop_wait"]
    monkeypatch.setattr(wait_tools.INPUT_ENGINE, "sleep", lambda time_ms: {"slept_ms": time_ms})
    STATE.stop()
    res = fn(time_ms=1)
    try:
        assert res["ok"] is True
        assert STATE.get().active is True
    finally:
        STATE.stop()


def test_orchestration_read_text_host_model_supported(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_ENABLE_HOST_OCR", "1")
    monkeypatch.setattr(
        orchestration_tools,
        "_read_text_host_model",
        lambda **kwargs: asyncio.sleep(0, result={"text": "abc", "chars": 3, "backend": "host_model", "confidence": None}),
    )
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "read_text", "backend": "host_model", "image_path": "dummy.png"}],
                settings=load_settings(),
                stop_on_error=True,
                ctx=_FakeCtx(),
            )
        )
        assert res["ok"] is True
        step = res["data"]["steps"][0]
        assert step["data"]["backend"] == "host_model"
    finally:
        STATE.stop()


def test_orchestration_unknown_action_noop_when_allowed():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "future_magic_action"}],
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

def test_allow_unknown_actions_does_not_bypass_known_action_validation():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "click"}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="balanced",
                runtime_options={"allow_unknown_actions": True},
            )
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "validation_error"
        assert "known actions" in str(res["error"]["message"]).lower()
    finally:
        STATE.stop()


def test_orchestration_host_model_without_ctx_best_effort(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_ENABLE_HOST_OCR", "1")
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "read_text", "backend": "host_model", "image_path": "missing.png"}],
                settings=load_settings(),
                stop_on_error=True,
                ctx=None,
            )
        )
        assert res["ok"] is True
        step = res["data"]["steps"][0]
        assert step["data"]["backend"] == "none"
    finally:
        STATE.stop()


def test_allowlist_soft_mode_does_not_block(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_REQUIRE_ALLOWLIST", "1")
    monkeypatch.setenv("NOVAFORGE_ALLOWLIST_HARD_ENFORCE", "0")
    settings = load_settings()
    assert_process_allowed(settings, "definitely-not-in-allowlist")


def test_emergency_stop_hard_blocks_until_cleared():
    settings = load_settings()
    STATE.start()
    try:
        STATE.set_emergency_stop(True)
        try:
            assert_can_run(settings)
            raise AssertionError("Expected assert_can_run to raise when emergency stop is active")
        except RuntimeError as e:
            assert "Emergency stop is active" in str(e)
        assert STATE.get().emergency_stop is True
    finally:
        STATE.stop()


def test_safe_mode_confirm_string_false_is_rejected():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "launch_app", "command": "notepad.exe", "process_name_for_policy": "notepad", "confirm": "false"}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
            )
        )
        assert res["ok"] is False
        assert "confirm=true" in str(res["error"]["message"])
    finally:
        STATE.stop()


def test_allow_unknown_actions_still_rejects_non_object_steps():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "future_magic_action"}, "bad-step"],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="balanced",
                runtime_options={"allow_unknown_actions": True},
            )
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "validation_error"
        details = res["error"].get("details", {})
        errors = details.get("errors", [])
        assert any(isinstance(e, dict) and e.get("index") == 1 for e in errors)
    finally:
        STATE.stop()
