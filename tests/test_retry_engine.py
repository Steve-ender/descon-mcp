from __future__ import annotations

import pytest

from novaforge.engines.retry_engine import retry_call


def test_retry_call_eventually_succeeds():
    state = {"n": 0}

    def op():
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    out = retry_call(op, timeout_ms=1000, interval_ms=10)
    assert out == "ok"
    assert state["n"] == 3


def test_retry_call_times_out():
    def op():
        raise RuntimeError("always-bad")

    with pytest.raises(RuntimeError) as exc:
        retry_call(op, timeout_ms=60, interval_ms=10)
    assert "Timed out after" in str(exc.value)
