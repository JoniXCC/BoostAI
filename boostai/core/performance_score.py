"""Transparent performance-health score.

This is *not* an industry benchmark. Each category is a documented piecewise-linear
function of measured values (see docs/SCORING.md); the overall score is their weighted
average over the categories that could actually be measured.

| Category        | Weight | Based on                                                  |
|-----------------|--------|-----------------------------------------------------------|
| Memory          | 0.25   | RAM %, commit %, hard faults/s, possible leaks            |
| CPU             | 0.20   | recent average CPU %, idle CPU %, interrupt/DPC time      |
| Responsiveness  | 0.20   | disk active %, disk latency, paging                        |
| Disk capacity   | 0.15   | free space on the system drive                            |
| Startup         | 0.10   | enabled optional / high-impact / unknown startup items   |
| Background load | 0.10   | process count vs. this PC's baseline (or absolute)        |
"""

from __future__ import annotations

from dataclasses import dataclass, field

WEIGHTS = {
    "Memory": 0.25,
    "CPU": 0.20,
    "Responsiveness": 0.20,
    "Disk capacity": 0.15,
    "Startup": 0.10,
    "Background load": 0.10,
}


def piecewise(x: float, points: list[tuple[float, float]]) -> float:
    """Linear interpolation through (x, y) points; clamps outside the range."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y1
    return points[-1][1]


RAM_CURVE = [(0, 100), (60, 100), (75, 85), (85, 65), (92, 40), (97, 15), (100, 0)]
COMMIT_CURVE = [(0, 100), (80, 100), (90, 70), (95, 40), (100, 0)]
CPU_CURVE = [(0, 100), (30, 100), (60, 80), (85, 50), (100, 20)]
IDLE_PENALTY = [(0, 0), (10, 0), (20, 10), (30, 20), (60, 30)]
DISK_FREE_CURVE = [(0, 0), (5, 20), (10, 50), (15, 75), (20, 90), (25, 100)]
BUSY_CURVE = [(0, 100), (50, 100), (80, 70), (95, 40), (100, 20)]
LATENCY_CURVE = [(0, 100), (20, 100), (50, 70), (100, 40), (200, 15)]
PROC_RATIO_CURVE = [(0, 100), (1.1, 100), (1.5, 60), (2.0, 30), (3.0, 10)]
PROC_ABS_CURVE = [(0, 100), (250, 100), (350, 85), (450, 60), (600, 30)]  # Windows 11 idles at ~200-300


@dataclass(slots=True)
class ScoreInputs:
    ram_percent: float
    commit_percent: float | None = None
    hard_faults_per_sec: float | None = None
    possible_leaks: int = 0
    cpu_avg_percent: float = 0.0
    idle_cpu_percent: float | None = None
    interrupt_dpc_percent: float = 0.0
    startup_high_impact: int = 0
    startup_optional: int = 0
    startup_unknown: int = 0
    startup_known: bool = True
    system_disk_free_percent: float | None = None
    system_disk_free_gb: float | None = None
    disk_busy_percent: float | None = None
    disk_latency_ms: float | None = None
    process_count: int = 0
    process_count_baseline: float | None = None


@dataclass(slots=True)
class CategoryScore:
    name: str
    score: float
    weight: float
    details: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HealthScore:
    overall: float
    label: str
    categories: dict[str, CategoryScore]

    def to_dict(self) -> dict:
        return {
            "overall": round(self.overall, 1),
            "label": self.label,
            "categories": {k: {"score": round(v.score, 1), "weight": v.weight, "details": v.details}
                           for k, v in self.categories.items()},
        }


def label_for(score: float) -> str:
    if score >= 85:
        return "Good"
    if score >= 70:
        return "Fair"
    if score >= 50:
        return "Needs attention"
    return "Poor"


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def compute_score(i: ScoreInputs) -> HealthScore:
    cats: dict[str, CategoryScore] = {}

    # Memory
    ram = piecewise(i.ram_percent, RAM_CURVE)
    details = [f"RAM {i.ram_percent:.0f}% -> {ram:.0f}"]
    mem = ram
    if i.commit_percent is not None:
        commit = piecewise(i.commit_percent, COMMIT_CURVE)
        details.append(f"commit {i.commit_percent:.0f}% -> {commit:.0f}")
        mem = min(mem, commit)
    if i.hard_faults_per_sec is not None and i.hard_faults_per_sec > 1000:
        penalty = 20 if i.hard_faults_per_sec > 3000 else 10
        mem -= penalty
        details.append(f"heavy paging -{penalty}")
    if i.possible_leaks:
        mem -= 10 * i.possible_leaks
        details.append(f"{i.possible_leaks} possible leak(s) -{10 * i.possible_leaks}")
    cats["Memory"] = CategoryScore("Memory", _clamp(mem), WEIGHTS["Memory"], details)

    # CPU
    cpu = piecewise(i.cpu_avg_percent, CPU_CURVE)
    details = [f"average CPU {i.cpu_avg_percent:.0f}% -> {cpu:.0f}"]
    if i.idle_cpu_percent is not None:
        pen = piecewise(i.idle_cpu_percent, IDLE_PENALTY)
        if pen:
            cpu -= pen
            details.append(f"idle CPU {i.idle_cpu_percent:.0f}% -{pen:.0f}")
    if i.interrupt_dpc_percent >= 5:
        pen = 25 if i.interrupt_dpc_percent >= 10 else 10
        cpu -= pen
        details.append(f"interrupt/DPC {i.interrupt_dpc_percent:.0f}% -{pen}")
    cats["CPU"] = CategoryScore("CPU", _clamp(cpu), WEIGHTS["CPU"], details)

    # Responsiveness (only if disk counters are available)
    parts = []
    details = []
    if i.disk_busy_percent is not None:
        b = piecewise(i.disk_busy_percent, BUSY_CURVE)
        parts.append(b)
        details.append(f"disk active {i.disk_busy_percent:.0f}% -> {b:.0f}")
    if i.disk_latency_ms is not None:
        lat = piecewise(i.disk_latency_ms, LATENCY_CURVE)
        parts.append(lat)
        details.append(f"disk latency {i.disk_latency_ms:.1f} ms -> {lat:.0f}")
    if parts:
        resp = min(parts)
        if i.hard_faults_per_sec is not None and i.hard_faults_per_sec > 1500:
            resp -= 15
            details.append("paging -15")
        cats["Responsiveness"] = CategoryScore("Responsiveness", _clamp(resp), WEIGHTS["Responsiveness"], details)

    # Disk capacity
    if i.system_disk_free_percent is not None:
        d = piecewise(i.system_disk_free_percent, DISK_FREE_CURVE)
        details = [f"{i.system_disk_free_percent:.0f}% free -> {d:.0f}"]
        if i.system_disk_free_gb is not None and i.system_disk_free_gb < 10:
            d = min(d, 40)
            details.append("under 10 GB free: capped at 40")
        cats["Disk capacity"] = CategoryScore("Disk capacity", _clamp(d), WEIGHTS["Disk capacity"], details)

    # Startup
    if i.startup_known:
        s = 100 - 10 * i.startup_high_impact - 4 * i.startup_optional - 2 * i.startup_unknown
        cats["Startup"] = CategoryScore("Startup", _clamp(s), WEIGHTS["Startup"], [
            f"{i.startup_high_impact} high-impact x10, {i.startup_optional} optional x4, {i.startup_unknown} unknown x2"])

    # Background load
    if i.process_count_baseline:
        ratio = i.process_count / i.process_count_baseline
        bg = piecewise(ratio, PROC_RATIO_CURVE)
        details = [f"{i.process_count} processes = {ratio:.2f}x this PC's usual -> {bg:.0f}"]
    else:
        bg = piecewise(i.process_count, PROC_ABS_CURVE)
        details = [f"{i.process_count} processes -> {bg:.0f} (no baseline yet)"]
    cats["Background load"] = CategoryScore("Background load", _clamp(bg), WEIGHTS["Background load"], details)

    total_weight = sum(c.weight for c in cats.values())
    overall = sum(c.score * c.weight for c in cats.values()) / total_weight if total_weight else 0.0
    return HealthScore(overall=round(overall, 1), label=label_for(overall), categories=cats)
