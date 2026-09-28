"""Physical memory, commit charge, pagefile and memory-pressure estimation."""

from __future__ import annotations

import psutil

from boostai.core.models import MemorySnapshot, PressureLevel
from boostai.utils import winapi


def collect_memory(
    hard_faults_per_sec: float | None = None, compressed_bytes: int | None = None, detailed: bool = True
) -> MemorySnapshot:
    """RAM + commit via one cheap GlobalMemoryStatusEx call.

    ``detailed`` adds the system cache size (GetPerformanceInfo) and page-file usage, which
    are comparatively expensive Windows queries, so the background monitor skips them.
    """
    ms = winapi.global_memory_status()
    if ms is None:
        vm = psutil.virtual_memory()
        ms = {"total": int(vm.total), "available": int(vm.available), "commit_total": None, "commit_limit": None}
    total, available = int(ms["total"]), int(ms["available"])
    cached = pagefile_total = pagefile_used = None
    if detailed:
        perf = winapi.get_performance_info() or {}
        cached = perf.get("system_cache")
        try:
            sw = psutil.swap_memory()
            pagefile_total, pagefile_used = int(sw.total), int(sw.used)
        except (OSError, RuntimeError):
            pass
    return MemorySnapshot(
        total=total,
        available=available,
        used=total - available,
        percent=round(100.0 * (total - available) / total, 1) if total else 0.0,
        cached=cached,
        commit_total=ms.get("commit_total"),
        commit_limit=ms.get("commit_limit"),
        pagefile_total=pagefile_total,
        pagefile_used=pagefile_used,
        hard_faults_per_sec=hard_faults_per_sec,
        compressed_bytes=compressed_bytes,
    )


def pressure_level(mem: MemorySnapshot) -> PressureLevel:
    """Deterministic memory-pressure classification.

    * CRITICAL: < 5 % RAM available, or commit charge > 95 % of the commit limit.
    * HIGH:     < 10 % available, commit > 90 %, or heavy hard faulting with < 20 % available.
    * ELEVATED: < 20 % available or commit > 80 %.
    * NORMAL:   otherwise.
    """
    avail_pct = 100.0 * mem.available / mem.total if mem.total else 100.0
    commit = mem.commit_percent or 0.0
    faults = mem.hard_faults_per_sec or 0.0
    if avail_pct < 5 or commit > 95:
        return PressureLevel.CRITICAL
    if avail_pct < 10 or commit > 90 or (faults > 1000 and avail_pct < 20):
        return PressureLevel.HIGH
    if avail_pct < 20 or commit > 80:
        return PressureLevel.ELEVATED
    return PressureLevel.NORMAL
