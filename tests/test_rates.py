"""Counter deltas: unknown on a reset or a changed set, never negative."""

from __future__ import annotations

import pytest

from dgx_status.rates import RateTracker, Sample, rate


def test_rate_sums_deltas_per_second() -> None:
    a = Sample(10.0, {"x": 100, "y": 1000})
    b = Sample(12.0, {"x": 300, "y": 1400})
    assert rate(a, b) == pytest.approx(300.0)


def test_first_sample_is_unknown() -> None:
    assert rate(None, Sample(1.0, {"x": 1})) is None
    assert rate(Sample(1.0, {"x": 1}), None) is None


def test_counter_that_goes_down_is_unknown() -> None:
    a = Sample(1.0, {"x": 100, "y": 5})
    assert rate(a, Sample(2.0, {"x": 50, "y": 900})) is None


def test_changed_set_is_unknown() -> None:
    a = Sample(1.0, {"x": 1, "y": 1})
    assert rate(a, Sample(2.0, {"x": 2})) is None
    assert rate(a, Sample(2.0, {"x": 2, "y": 2, "z": 0})) is None


def test_no_time_or_no_counters_is_unknown() -> None:
    a = Sample(1.0, {"x": 1})
    assert rate(a, Sample(1.0, {"x": 5})) is None
    assert rate(a, Sample(0.5, {"x": 5})) is None
    assert rate(Sample(1.0, {}), Sample(2.0, {})) is None


def test_tracker_scales_and_recovers_after_a_reset() -> None:
    t = RateTracker(scale=8.0)
    assert t.update(Sample(0.0, {"x": 0})) is None
    assert t.update(Sample(1.0, {"x": 125})) == pytest.approx(1000.0)
    assert t.update(Sample(2.0, {"x": 3})) is None  # reset
    assert t.update(Sample(3.0, {"x": 128})) == pytest.approx(1000.0)
    assert t.update(None) is None
    assert t.update(Sample(4.0, {"x": 200})) is None  # no baseline after a gap
