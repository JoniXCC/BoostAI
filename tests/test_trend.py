import math

import pytest

from boostai.core.trend import analyze_trend, downsample, linear_fit, max_drawdown, non_decreasing_ratio, percentile


def test_linear_fit_perfect_line():
    slope, intercept, r2 = linear_fit([0, 1, 2, 3], [1, 3, 5, 7])
    assert slope == pytest.approx(2)
    assert intercept == pytest.approx(1)
    assert r2 == pytest.approx(1)


def test_linear_fit_flat_and_degenerate():
    assert linear_fit([0, 1, 2], [5, 5, 5])[0] == 0
    assert linear_fit([1], [3]) == (0.0, 3.0, 0.0)
    assert linear_fit([2, 2, 2], [1, 2, 3])[2] == 0.0


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile([10], 95) == 10
    assert percentile([1, 2, 3, 4, 5], 100) == 5
    with pytest.raises(ValueError):
        percentile([], 50)


def test_drawdown_and_ratio():
    assert max_drawdown([1, 5, 3, 6, 2]) == 4
    assert max_drawdown([1, 2, 3]) == 0
    assert non_decreasing_ratio([1, 2, 2, 3, 2.5], tolerance=0.1) == pytest.approx(0.75)


def test_downsample_keeps_last_point():
    pts = [(i, i) for i in range(1000)]
    out = downsample(pts, 100)
    assert len(out) == 100 and out[-1] == pts[-1]


def test_analyze_trend_growth_metrics():
    pts = [(i * 60.0, 1000.0 + 100 * i) for i in range(31)]  # +100/min for 30 min
    t = analyze_trend(pts, noise_tolerance=1)
    assert t.duration_s == 1800
    assert t.growth == 3000
    assert t.slope_per_min == pytest.approx(100)
    assert t.r_squared == pytest.approx(1)
    assert t.release_ratio == 0
    assert t.relative_growth == pytest.approx(3.0)


def test_analyze_trend_from_zero_start_is_infinite_relative():
    t = analyze_trend([(0, 0.0), (60, 10.0)], 0)
    assert math.isinf(t.relative_growth)
    assert analyze_trend([(0, 1.0)], 0) is None
