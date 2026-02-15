from __future__ import annotations

from descon.schemas import validate_plan_with_errors


def test_schema_accepts_focus_guard_step():
    plan = [
        {
            "action": "focus_guard",
            "window_handle": 12345,
            "mismatch_mode": "warn",
            "retries": 2,
            "retry_wait_ms": 100,
        }
    ]
    validated, errs = validate_plan_with_errors(plan)
    assert errs == []
    assert validated is not None
    assert validated[0]["action"] == "focus_guard"


def test_schema_rejects_invalid_mismatch_mode():
    plan = [{"action": "focus_guard", "window_handle": 12345, "mismatch_mode": "invalid"}]
    validated, errs = validate_plan_with_errors(plan)
    assert validated is None
    assert errs


def test_schema_accepts_if_step():
    plan = [{"action": "if", "condition": "text_visible", "text": "OK", "then": [{"action": "wait", "time_ms": 100}]}]
    validated, errs = validate_plan_with_errors(plan)
    assert errs == []
    assert validated is not None
    assert validated[0]["action"] == "if"


def test_schema_accepts_repeat_step():
    plan = [{"action": "repeat", "count": 3, "steps": [{"action": "wait", "time_ms": 100}]}]
    validated, errs = validate_plan_with_errors(plan)
    assert errs == []
    assert validated is not None
    assert validated[0]["action"] == "repeat"


def test_schema_accepts_while_step():
    plan = [{"action": "while", "condition": "window_exists", "title_regex": "Notepad", "steps": [{"action": "wait", "time_ms": 100}], "max_iterations": 5}]
    validated, errs = validate_plan_with_errors(plan)
    assert errs == []
    assert validated is not None
    assert validated[0]["action"] == "while"
