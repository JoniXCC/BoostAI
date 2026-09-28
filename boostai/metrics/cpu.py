"""CPU utilisation sampling based on our own cpu_times deltas.

psutil's module-level ``cpu_percent()`` keeps global state, so two callers on
different threads would corrupt each other's intervals. Each :class:`CpuSampler`
keeps its own previous reading instead.
"""

from __future__ import annotations

import threading
import time

import psutil

from boostai.core.models import CpuSnapshot


def _busy_fraction(prev, cur) -> tuple[float, float]:
    total = sum(cur) - sum(prev)
    if total <= 0:
        return 0.0, 0.0
    idle = cur.idle - prev.idle
    return max(0.0, min(1.0, 1.0 - idle / total)), total


class CpuSampler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev_total = psutil.cpu_times()
        self._prev_cores = psutil.cpu_times(percpu=True)
        self._prev_time = time.monotonic()

    def sample(self, min_interval: float = 0.25) -> CpuSnapshot:
        with self._lock:
            elapsed = time.monotonic() - self._prev_time
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            cur_total = psutil.cpu_times()
            cur_cores = psutil.cpu_times(percpu=True)

            busy, total_delta = _busy_fraction(self._prev_total, cur_total)
            per_core = [round(100 * _busy_fraction(p, c)[0], 1) for p, c in zip(self._prev_cores, cur_cores)]
            interrupt = dpc = 0.0
            if total_delta > 0:
                interrupt = 100 * (getattr(cur_total, "interrupt", 0) - getattr(self._prev_total, "interrupt", 0)) / total_delta
                dpc = 100 * (getattr(cur_total, "dpc", 0) - getattr(self._prev_total, "dpc", 0)) / total_delta

            self._prev_total, self._prev_cores, self._prev_time = cur_total, cur_cores, time.monotonic()

        freq = None
        try:
            f = psutil.cpu_freq()
            freq = float(f.current) if f else None
        except (OSError, RuntimeError, NotImplementedError):
            freq = None
        return CpuSnapshot(
            total_percent=round(100 * busy, 1),
            per_core=per_core,
            interrupt_percent=round(max(0.0, interrupt), 2),
            dpc_percent=round(max(0.0, dpc), 2),
            frequency_mhz=freq,
        )
