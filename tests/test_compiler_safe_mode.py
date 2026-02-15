from __future__ import annotations

from descon.tools.compiler_tools import _compile_intent


def test_compile_launch_app_injects_confirm_true_by_default():
    steps, _ = _compile_intent(
        intent={"intent": "launch_app", "command": "notepad.exe"},
        idx=0,
        default_profile="balanced",
    )
    assert steps[0]["action"] == "launch_app"
    assert steps[0]["confirm"] is True


def test_compile_close_app_injects_confirm_true_by_default():
    steps, _ = _compile_intent(
        intent={"intent": "close_app", "name_filter": "notepad"},
        idx=0,
        default_profile="balanced",
    )
    assert steps[0]["action"] == "close_app"
    assert steps[0]["confirm"] is True


def test_compile_launch_app_confirm_string_false_is_false():
    steps, _ = _compile_intent(
        intent={"intent": "launch_app", "command": "notepad.exe", "confirm": "false"},
        idx=0,
        default_profile="balanced",
    )
    assert steps[0]["confirm"] is False


def test_compile_element_action_allow_fallback_string_false_is_false():
    steps, _ = _compile_intent(
        intent={
            "intent": "element_action",
            "element_action": "click",
            "window_title_regex": ".*",
            "title": "OK",
            "allow_fallback": "false",
        },
        idx=0,
        default_profile="balanced",
    )
    assert steps[0]["allow_fallback"] is False


def test_compile_transaction_end_committed_string_false_is_false():
    steps, _ = _compile_intent(
        intent={"intent": "transaction_end", "committed": "false"},
        idx=0,
        default_profile="balanced",
    )
    assert steps[0]["committed"] is False
