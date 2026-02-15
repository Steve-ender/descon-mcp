from __future__ import annotations

import asyncio

from descon.config import load_settings
from descon.failure_envelope import analyze_failure_envelope
from descon.state import STATE
import descon.tools.orchestration_tools as orchestration_tools


def test_failure_envelope_detects_safe_mode_confirm_blocker():
    envelope = analyze_failure_envelope(
        plan=[{"action": "launch_app", "command": "notepad.exe"}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
    )
    assert envelope["ready"] is False
    codes = {i["code"] for i in envelope["hard_blockers"]}
    assert "safe_mode_confirm_required" in codes


def test_execute_plan_includes_preflight_payload():
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
        assert "preflight" in res["data"]
        assert isinstance(res["data"]["preflight"], dict)
    finally:
        STATE.stop()


def test_execute_plan_enforce_preflight_blocks_hard_blockers():
    STATE.start()
    try:
        res = asyncio.run(
            orchestration_tools.execute_plan(
                plan=[{"action": "launch_app", "command": "notepad.exe"}],
                settings=load_settings(),
                stop_on_error=True,
                runtime_profile="basic_reliable",
                enforce_preflight=True,
            )
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "preflight_blocked"
        assert "preflight" in res["error"]["details"]
    finally:
        STATE.stop()


def test_failure_envelope_flags_invalid_step_type():
    envelope = analyze_failure_envelope(
        plan=[{"action": "wait", "time_ms": 1}, "bad-step"],
        settings=load_settings(),
        runtime_profile="basic_reliable",
    )
    codes = {i["code"] for i in envelope["hard_blockers"]}
    assert "invalid_step_type" in codes


def test_failure_envelope_flags_force_broker_when_disabled(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_ENABLE_ELEVATED_BROKER", "0")
    envelope = analyze_failure_envelope(
        plan=[{"action": "click", "x": 1, "y": 1, "force_broker": True}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
    )
    codes = {i["code"] for i in envelope["hard_blockers"]}
    assert "force_broker_without_broker" in codes


def test_failure_envelope_flags_conflicting_routing_directives():
    envelope = analyze_failure_envelope(
        plan=[{"action": "click", "x": 1, "y": 1, "force_local": True, "force_broker": True}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
    )
    codes = {i["code"] for i in envelope["hard_blockers"]}
    assert "conflicting_routing_directives" in codes


def test_failure_envelope_flags_broker_mode_always_without_broker(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_ENABLE_ELEVATED_BROKER", "0")
    envelope = analyze_failure_envelope(
        plan=[{"action": "click", "x": 1, "y": 1}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
        runtime_options={"broker_mode": "always"},
    )
    codes = {i["code"] for i in envelope["hard_blockers"]}
    assert "broker_mode_always_without_broker" in codes


def test_failure_envelope_broker_mode_always_blocker_is_not_duplicated(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_ENABLE_ELEVATED_BROKER", "0")
    envelope = analyze_failure_envelope(
        plan=[{"action": "wait", "time_ms": 1}, {"action": "click", "x": 1, "y": 1}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
        runtime_options={"broker_mode": "always"},
    )
    codes = [i["code"] for i in envelope["hard_blockers"]]
    assert codes.count("broker_mode_always_without_broker") == 1


def test_failure_envelope_string_false_flags_do_not_trigger_force_routing(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_ENABLE_ELEVATED_BROKER", "0")
    envelope = analyze_failure_envelope(
        plan=[{"action": "wait", "force_broker": "false", "force_local": "false"}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
    )
    codes = {i["code"] for i in envelope["hard_blockers"]}
    assert "force_broker_without_broker" not in codes
    assert "conflicting_routing_directives" not in codes


def test_failure_envelope_safe_mode_confirm_string_false_blocks():
    envelope = analyze_failure_envelope(
        plan=[{"action": "close_app", "name_filter": "notepad", "confirm": "false"}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
    )
    codes = {i["code"] for i in envelope["hard_blockers"]}
    assert "safe_mode_confirm_required" in codes


def test_failure_envelope_flags_invalid_broker_mode_warning():
    envelope = analyze_failure_envelope(
        plan=[{"action": "wait", "time_ms": 1}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
        runtime_options={"broker_mode": "unexpected-mode"},
    )
    warn_codes = {i["code"] for i in envelope["warnings"]}
    assert "invalid_broker_mode" in warn_codes


def test_failure_envelope_does_not_warn_for_non_routable_in_broker_mode_always():
    envelope = analyze_failure_envelope(
        plan=[{"action": "wait", "time_ms": 1}],
        settings=load_settings(),
        runtime_profile="basic_reliable",
        runtime_options={"broker_mode": "always"},
    )
    warn_codes = {i["code"] for i in envelope["warnings"]}
    assert "broker_mode_always_non_routable" not in warn_codes
