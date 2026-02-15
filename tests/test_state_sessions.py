from __future__ import annotations

from descon.config import load_settings
from descon.safety import assert_can_run
from descon.state import STATE
import descon.tools.session_tools as session_tools


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _cleanup_sessions() -> None:
    for sid in list(STATE.list_session_ids()):
        STATE.stop(session_id=sid)
    STATE.set_default_session("default")
    STATE.clear_thread_session()


def test_state_isolates_named_sessions() -> None:
    _cleanup_sessions()
    try:
        STATE.start(session_id="alpha")
        STATE.mark_action(session_id="alpha")
        STATE.start(session_id="beta")
        STATE.mark_action(session_id="beta")
        STATE.mark_action(session_id="beta")

        alpha = STATE.get(session_id="alpha")
        beta = STATE.get(session_id="beta")

        assert alpha.active is True
        assert beta.active is True
        assert alpha.action_count == 1
        assert beta.action_count == 2

        STATE.stop(session_id="beta")
        assert STATE.get(session_id="alpha").active is True
        assert STATE.get(session_id="beta").active is False
    finally:
        _cleanup_sessions()


def test_assert_can_run_autostarts_specific_session() -> None:
    _cleanup_sessions()
    settings = load_settings()
    try:
        resolved = assert_can_run(settings=settings, session_id="iso_autostart")
        assert resolved == "iso_autostart"
        assert STATE.get(session_id="iso_autostart").active is True
        assert STATE.get(session_id="default").active is False
    finally:
        _cleanup_sessions()


def test_session_tools_support_session_id() -> None:
    _cleanup_sessions()
    fake = _FakeMCP()
    session_tools.register_session_tools(fake)
    tool = fake.tools["desktop_session"]
    try:
        res_alpha = tool(action="start", mode="restart", session_id="alpha")
        assert res_alpha["ok"] is True
        assert res_alpha["data"]["session_id"] == "alpha"

        res_beta = tool(action="start", mode="restart", session_id="beta")
        assert res_beta["ok"] is True
        assert res_beta["data"]["session_id"] == "beta"
        assert res_beta["data"]["default_session_id"] == "beta"

        st_alpha = tool(action="status", session_id="alpha")
        assert st_alpha["ok"] is True
        assert st_alpha["data"]["session_id"] == "alpha"
        assert st_alpha["data"]["active"] is True

        tool(action="stop", session_id="beta")
        st_alpha_after = tool(action="status", session_id="alpha")
        assert st_alpha_after["data"]["active"] is True
    finally:
        tool(action="stop", session_id="alpha")
        tool(action="stop", session_id="beta")
        _cleanup_sessions()
