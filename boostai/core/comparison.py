"""Before/after measurement and comparison.

Both measurements use the *same* method (CPU averaged over the same window length,
memory read at the end of the window) so the numbers are comparable. Differences
smaller than a noise threshold are reported as "no meaningful change" - BoostAI never
presents noise as an improvement.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Callable

from boostai.core.models import MB, fmt_bytes
from boostai.core.trend import mean

PRESSURE_ORDER = ["NORMAL", "ELEVATED", "HIGH", "CRITICAL"]


@dataclass(slots=True)
class MeasuredState:
    timestamp: float
    ram_used: int
    ram_total: int
    ram_percent: float
    available: int
    cpu_avg: float
    process_count: int
    pressure: str
    commit_percent: float | None
    window_seconds: float

    def to_dict(self) -> dict:
        return asdict(self)


def measure_state(sampler, seconds: float = 10.0, sleep: Callable[[float], None] = time.sleep) -> MeasuredState:
    """Average CPU over ``seconds`` (1 s steps) and read memory/process count at the end."""
    from boostai.metrics.cpu import CpuSampler

    samples = []
    steps = max(1, int(seconds))
    cpu = CpuSampler()  # private sampler so the background monitor cannot shorten our intervals
    for _ in range(steps):
        sleep(1.0)
        samples.append(cpu.sample(min_interval=0.0).total_percent)
    snap = sampler.snapshot(include_processes=True)
    return MeasuredState(
        timestamp=snap.timestamp, ram_used=snap.memory.used, ram_total=snap.memory.total,
        ram_percent=snap.memory.percent, available=snap.memory.available, cpu_avg=round(mean(samples), 1),
        process_count=snap.process_count, pressure=snap.memory_pressure.value,
        commit_percent=snap.memory.commit_percent, window_seconds=float(steps),
    )


@dataclass(slots=True)
class ComparisonRow:
    metric: str
    before: str
    after: str
    change: str
    verdict: str          # "improved" | "worse" | "no meaningful change"


@dataclass(slots=True)
class Comparison:
    rows: list[ComparisonRow]
    highlights: list[str]    # only measured, meaningful improvements

    def to_dict(self) -> dict:
        return {"rows": [asdict(r) for r in self.rows], "highlights": self.highlights}


def compare(before: MeasuredState, after: MeasuredState) -> Comparison:
    rows: list[ComparisonRow] = []
    highlights: list[str] = []
    ram_noise = max(100 * MB, 0.01 * before.ram_total)

    recovered = before.ram_used - after.ram_used
    verdict = _verdict(recovered, ram_noise)
    rows.append(ComparisonRow("RAM in use", f"{fmt_bytes(before.ram_used)} / {fmt_bytes(before.ram_total)}",
                              f"{fmt_bytes(after.ram_used)} / {fmt_bytes(after.ram_total)}",
                              f"{fmt_bytes(-recovered)}", verdict))
    if verdict == "improved":
        highlights.append(f"Recovered memory: {fmt_bytes(recovered)}")

    cpu_delta = before.cpu_avg - after.cpu_avg
    verdict = _verdict(cpu_delta, 3.0)
    rows.append(ComparisonRow(f"CPU ({int(before.window_seconds)}-s average)", f"{before.cpu_avg:.0f}%",
                              f"{after.cpu_avg:.0f}%", f"{-cpu_delta:+.0f} pts", verdict))
    if verdict == "improved":
        highlights.append(f"CPU load reduction: {cpu_delta:.0f} percentage points")

    proc_delta = before.process_count - after.process_count
    verdict = _verdict(proc_delta, 3)
    rows.append(ComparisonRow("Running processes", str(before.process_count), str(after.process_count),
                              f"{-proc_delta:+d}", verdict))
    if verdict == "improved":
        highlights.append(f"Processes reduced: {proc_delta}")

    b, a = PRESSURE_ORDER.index(before.pressure), PRESSURE_ORDER.index(after.pressure)
    verdict = "improved" if a < b else "worse" if a > b else "no meaningful change"
    rows.append(ComparisonRow("Memory pressure", before.pressure, after.pressure,
                              "" if a == b else f"{before.pressure} -> {after.pressure}", verdict))
    if verdict == "improved":
        highlights.append(f"Memory pressure: {before.pressure} -> {after.pressure}")

    if before.commit_percent is not None and after.commit_percent is not None:
        d = before.commit_percent - after.commit_percent
        verdict = _verdict(d, 2.0)
        rows.append(ComparisonRow("Commit charge", f"{before.commit_percent:.0f}%", f"{after.commit_percent:.0f}%",
                                  f"{-d:+.0f} pts", verdict))
    return Comparison(rows=rows, highlights=highlights)


def _verdict(improvement: float, noise: float) -> str:
    if improvement >= noise:
        return "improved"
    if improvement <= -noise:
        return "worse"
    return "no meaningful change"
