from __future__ import annotations

import pytest

from descon.execution_runtime import RuntimeController, policy_from_options


def test_policy_profiles():
    strict = policy_from_options("strict")
    unrestricted = policy_from_options("unrestricted")
    balanced = policy_from_options("balanced")
    assert strict.safe_mode is True
    assert strict.allow_unknown_actions is False
    assert strict.canary_checks is True
    assert strict.auto_fallback is False
    assert unrestricted.max_steps > balanced.max_steps
    assert unrestricted.observe_before_risky is False
    assert unrestricted.canary_checks is False


def test_runtime_budget_guard():
    rc = RuntimeController(policy_from_options("strict", {"max_steps": 1}))
    rc.check_budget(0)
    with pytest.raises(RuntimeError, match="Step budget exceeded"):
        rc.check_budget(1)


def test_runtime_safe_mode_confirmation_required():
    rc = RuntimeController(policy_from_options("strict"))
    with pytest.raises(RuntimeError, match="confirm=true"):
        rc.ensure_allowed_action(action="close_app", confirmed=False)
    rc.ensure_allowed_action(action="close_app", confirmed=True)


def test_policy_option_string_bool_coercion():
    p = policy_from_options(
        "balanced",
        {
            "observe_before_risky": "false",
            "safe_mode": "0",
            "allow_unknown_actions": "true",
            "canary_checks": "0",
            "auto_fallback": "1",
            "fallback_policy": "aggressive",
        },
    )
    assert p.observe_before_risky is False
    assert p.safe_mode is False
    assert p.allow_unknown_actions is True
    assert p.canary_checks is False
    assert p.auto_fallback is True
    assert p.fallback_policy == "aggressive"
