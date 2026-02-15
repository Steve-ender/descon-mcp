from __future__ import annotations

import asyncio

from descon.config import load_settings
from descon.state import STATE
import descon.tools.orchestration_tools as orchestration_tools


def _run(plan, runtime_options=None):
    return asyncio.run(
        orchestration_tools.execute_plan(
            plan=plan,
            settings=load_settings(),
            stop_on_error=True,
            runtime_options=runtime_options or {"canary_checks": False, "auto_fallback": False},
        )
    )


def test_execute_plan_wait_success():
    STATE.start()
    try:
        res = _run([{"action": "wait", "time_ms": 10}])
        assert res["ok"] is True
        assert res["data"]["succeeded"] is True
    finally:
        STATE.stop()


def test_fault_foreground_target_missing_returns_specific_code():
    STATE.start()
    try:
        res = _run(
            [
                {
                    "action": "element_action",
                    "element_action": "click",
                    "require_foreground_match": True,
                }
            ]
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "foreground_unresolvable_target"
    finally:
        STATE.stop()


def test_fault_focus_theft_after_action_maps_to_refocus_code(monkeypatch):
    calls = {"n": 0}

    def fake_guard(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "matched": True}
        raise RuntimeError("Foreground refocus failed after 3 attempts.")

    monkeypatch.setattr(orchestration_tools.WINDOW_ENGINE, "ensure_foreground_target", fake_guard)
    monkeypatch.setattr(
        orchestration_tools.WINDOW_ENGINE,
        "perform_element_action",
        lambda **kwargs: {"strategy": "mock"},
    )

    STATE.start()
    try:
        res = _run(
            [
                {
                    "action": "element_action",
                    "element_action": "click",
                    "window_handle": 9999,
                    "require_foreground_match": True,
                }
            ]
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "foreground_refocus_failed"
    finally:
        STATE.stop()


def test_fault_missing_window_maps_to_not_found(monkeypatch):
    monkeypatch.setattr(
        orchestration_tools.WINDOW_ENGINE,
        "perform_element_action",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("No window matched regex: .*Notepad.*")),
    )

    STATE.start()
    try:
        res = _run(
            [
                {
                    "action": "element_action",
                    "element_action": "click",
                    "window_title_regex": ".*Notepad.*",
                    "require_foreground_match": False,
                    "allow_fallback": False,
                }
            ]
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "not_found"
    finally:
        STATE.stop()


def test_fault_foreground_mismatch_maps_to_specific_code(monkeypatch):
    monkeypatch.setattr(
        orchestration_tools.WINDOW_ENGINE,
        "ensure_foreground_target",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Foreground mismatch (handle_mismatch).")),
    )

    STATE.start()
    try:
        res = _run(
            [
                {
                    "action": "element_action",
                    "element_action": "click",
                    "window_handle": 9999,
                    "require_foreground_match": True,
                    "foreground_mismatch_mode": "fail",
                }
            ]
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "foreground_mismatch"
    finally:
        STATE.stop()


def test_fault_text_contains_requires_query(monkeypatch):
    monkeypatch.setattr(
        orchestration_tools.WINDOW_ENGINE,
        "snapshot_element",
        lambda **kwargs: {"text_candidates": ["abc"], "title": "abc", "visible": True, "enabled": True, "focused": True},
    )
    STATE.start()
    try:
        res = _run([{"action": "assert", "condition": "text_contains", "window_handle": 1}])
        assert res["ok"] is False
        assert res["error"]["code"] == "validation_error"
    finally:
        STATE.stop()


def test_fault_invalid_window_handle_maps_to_validation_error():
    STATE.start()
    try:
        res = _run([{"action": "focus_guard", "window_handle": "abc"}])
        assert res["ok"] is False
        assert res["error"]["code"] == "validation_error"
    finally:
        STATE.stop()


def test_fault_low_confidence_text_maps_specific_code(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_BEST_EFFORT_OCR", "0")
    monkeypatch.setattr(
        orchestration_tools.OCR_ENGINE,
        "read_text_stable",
        lambda **kwargs: {
            "text": "Sove",
            "chars": 4,
            "backend": "local_model",
            "consensus_count": 1,
            "attempt_count": 3,
            "low_confidence": True,
            "errors": [],
        },
    )
    STATE.start()
    try:
        res = _run([{"action": "read_text", "image_path": "fake.png", "backend": "local_model"}])
        assert res["ok"] is False
        assert res["error"]["code"] == "low_confidence_text"
    finally:
        STATE.stop()


def test_canary_read_text_missing_image_maps_path_missing(monkeypatch):
    monkeypatch.setenv("NOVAFORGE_BEST_EFFORT_OCR", "0")
    STATE.start()
    try:
        res = _run(
            [{"action": "read_text", "image_path": "__definitely_missing__.png", "backend": "auto"}],
            runtime_options={"canary_checks": True},
        )
        assert res["ok"] is False
        assert res["error"]["code"] == "path_missing"
    finally:
        STATE.stop()


def test_auto_fallback_policy_promotes_read_text_backend(monkeypatch):
    seen = {"backend": None}

    def _fake_read_text_stable(**kwargs):
        seen["backend"] = kwargs.get("backend")
        return {
            "text": "ok",
            "chars": 2,
            "backend": kwargs.get("backend"),
            "consensus_count": 2,
            "attempt_count": 2,
            "low_confidence": False,
            "errors": [],
        }

    monkeypatch.setattr(orchestration_tools.OCR_ENGINE, "read_text_stable", _fake_read_text_stable)
    monkeypatch.setattr(orchestration_tools.Path, "exists", lambda self: True)

    STATE.start()
    try:
        res = _run(
            [{"action": "read_text", "image_path": "fake.png", "backend": "local_model"}],
            runtime_options={"canary_checks": True, "auto_fallback": True, "fallback_policy": "conservative"},
        )
        assert res["ok"] is True
        assert seen["backend"] == "auto"
    finally:
        STATE.stop()
