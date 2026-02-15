from __future__ import annotations

import descon.tools.action_tools as action_tools
import descon.tools.orchestration_tools as orchestration_tools
import descon.tools.window_tools as window_tools


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def test_orchestration_error_mapping_matrix():
    cases = [
        ("Foreground mismatch (handle_mismatch)", "foreground_mismatch"),
        ("Foreground refocus failed after 3 attempts", "foreground_refocus_failed"),
        ("Foreground guard requires a window target", "foreground_unresolvable_target"),
        ("No window matched regex: .*", "not_found"),
        ("Step timed out: wait_for", "timeout"),
        ("Unsupported action", "unsupported_action"),
        ("Process blocked by allowlist policy", "policy_blocked"),
    ]
    for msg, expected in cases:
        assert orchestration_tools._classify_error(RuntimeError(msg)) == expected


def test_action_error_mapping_matrix():
    cases = [
        ("Foreground mismatch (handle_mismatch)", "foreground_mismatch"),
        ("Foreground refocus failed after 3 attempts", "foreground_refocus_failed"),
        ("Foreground guard requires a window target", "foreground_unresolvable_target"),
        ("No window matched regex: .*", "not_found"),
        ("timed out after N attempts", "timeout"),
        ("allowlist blocked", "policy_blocked"),
        ("unsupported action", "unsupported_action"),
    ]
    for msg, expected in cases:
        assert action_tools._err_code(RuntimeError(msg)) == expected


def test_window_tool_error_mapping_via_focus_guard(monkeypatch):
    fake = _FakeMCP()
    window_tools.register_window_tools(fake)
    fn = fake.tools["desktop_window"]

    # Bypass session requirement for this focused unit test.
    monkeypatch.setattr(window_tools, "assert_can_run", lambda settings, session_id=None: "default")
    monkeypatch.setattr(window_tools.STATE, "mark_action", lambda session_id=None: 1)

    monkeypatch.setattr(
        window_tools.WINDOW_ENGINE,
        "ensure_foreground_target",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Foreground refocus failed after 3 attempts")),
    )
    res = fn(action="focus_guard", window_handle=1)
    assert res["ok"] is False
    assert res["error"]["code"] == "foreground_refocus_failed"
