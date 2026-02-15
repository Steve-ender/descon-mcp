from __future__ import annotations

from scripts.reliability_eval import (
    _compare_baseline,
    _default_live_desktop_scenarios,
    _flakiness,
    _max_consecutive_failures,
    _mttr_attempts,
)


def test_mttr_attempts_recovers_and_counts_unrecovered():
    # pass/fail series for expectation_ok values.
    series = [True, False, False, True, False, False, False]
    m = _mttr_attempts(series)
    assert m["recovered_failures"] == 2
    assert m["unrecovered_failures"] == 3
    assert m["recovery_attempts"] == [2, 1]
    assert m["mttr_attempts"] == 1.5


def test_flakiness_and_max_consecutive_failures():
    series = [True, False, True, False, False, False, True]
    assert _flakiness(series) == 0.6667
    assert _max_consecutive_failures(series) == 3


def test_compare_baseline_detects_regression():
    current = [{"scenario": "s1", "pass_rate": 0.9, "p95_timing_ms": 120}]
    baseline = {"summary": [{"scenario": "s1", "pass_rate": 0.95, "p95_timing_ms": 100}]}
    out = _compare_baseline(current, baseline)
    assert out["has_regression"] is True
    assert len(out["regressions"]) == 1


def test_live_desktop_preset_is_available():
    scenarios = _default_live_desktop_scenarios()
    names = [s.name for s in scenarios]
    assert len(scenarios) >= 2
    assert "live_notepad_launch_type_close" in names
