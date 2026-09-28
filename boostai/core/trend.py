"""Pure statistics used by the detectors (no I/O, fully unit-tested)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile (pct in 0..100)."""
    if not values:
        raise ValueError("percentile of empty sequence")
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    k = (len(data) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(data[int(k)])
    return float(data[lo] + (data[hi] - data[lo]) * (k - lo))


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float, float]:
    """Ordinary least squares. Returns (slope, intercept, r_squared)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0, float(ys[0]) if ys else 0.0, 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    if ss_tot == 0:
        return slope, intercept, 1.0 if slope == 0 else 0.0
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return slope, intercept, max(0.0, 1.0 - ss_res / ss_tot)


def non_decreasing_ratio(ys: Sequence[float], tolerance: float) -> float:
    """Share of consecutive steps that did not drop by more than ``tolerance``."""
    if len(ys) < 2:
        return 0.0
    steps = [b - a for a, b in zip(ys, ys[1:])]
    return sum(1 for s in steps if s >= -tolerance) / len(steps)


def max_drawdown(ys: Sequence[float]) -> float:
    """Largest drop from a running peak (how much memory was ever released)."""
    peak = -math.inf
    worst = 0.0
    for y in ys:
        peak = max(peak, y)
        worst = max(worst, peak - y)
    return worst


def downsample(points: Sequence[tuple[float, float]], max_points: int) -> list[tuple[float, float]]:
    if len(points) <= max_points:
        return list(points)
    step = len(points) / max_points
    out = [points[int(i * step)] for i in range(max_points)]
    if out[-1] != points[-1]:
        out[-1] = points[-1]
    return out


@dataclass(slots=True)
class TrendAnalysis:
    samples: int
    duration_s: float
    start_value: float
    end_value: float
    peak_value: float
    growth: float                 # end - start
    relative_growth: float        # growth / start
    slope_per_min: float          # least-squares units per minute
    r_squared: float
    non_decreasing_ratio: float
    max_release: float            # largest drawdown
    release_ratio: float          # max_release / max(growth, 1)


def analyze_trend(points: Sequence[tuple[float, float]], noise_tolerance: float) -> TrendAnalysis | None:
    """Describe the growth pattern of a (timestamp, value) series."""
    if len(points) < 2:
        return None
    pts = downsample(sorted(points), 240)
    t0 = pts[0][0]
    xs = [(t - t0) / 60.0 for t, _ in pts]
    ys = [float(v) for _, v in pts]
    slope, _, r2 = linear_fit(xs, ys)
    start, end = ys[0], ys[-1]
    growth = end - start
    release = max_drawdown(ys)
    return TrendAnalysis(
        samples=len(points),
        duration_s=pts[-1][0] - t0,
        start_value=start,
        end_value=end,
        peak_value=max(ys),
        growth=growth,
        relative_growth=growth / start if start > 0 else math.inf,
        slope_per_min=slope,
        r_squared=r2,
        non_decreasing_ratio=non_decreasing_ratio(ys, noise_tolerance),
        max_release=release,
        release_ratio=release / max(growth, 1.0),
    )
