"""Bounded in-memory time series fed by the monitor and read by the detectors.

Memory use is bounded on purpose: per-instance series are only kept for processes with
at least ``track_threshold`` bytes of private memory (small processes cannot produce a
meaningful leak signal), and every series is a fixed-length deque.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field

from boostai.core.models import MB, ProcessInfo, SystemSnapshot


@dataclass(slots=True)
class SystemPoint:
    t: float
    cpu: float
    ram_percent: float
    available: int
    commit_percent: float | None
    disk_read_bps: float
    disk_write_bps: float
    disk_busy: float | None
    process_count: int
    user_idle: float | None
    interrupt_dpc: float
    hard_faults: float | None
    fullscreen: bool = False


@dataclass(slots=True)
class InstanceSeries:
    name: str
    pid: int
    create_time: float
    points: deque = field(default_factory=deque)   # (t, private_bytes, cpu_percent, rss)
    last_seen: float = 0.0
    exe: str | None = None


class History:
    def __init__(self, history_minutes: int = 120, process_interval_s: float = 10.0,
                 system_interval_s: float = 2.0, track_threshold: int = 50 * MB) -> None:
        self._lock = threading.RLock()
        self.track_threshold = track_threshold
        self.proc_maxlen = max(30, int(history_minutes * 60 / process_interval_s))
        self.sys_maxlen = max(60, int(history_minutes * 60 / system_interval_s))
        self.system: deque[SystemPoint] = deque(maxlen=self.sys_maxlen)
        self.instances: dict[tuple[int, float], InstanceSeries] = {}
        self.groups: dict[str, deque] = {}            # lower name -> (t, private_sum, cpu_sum, count)
        self.group_display: dict[str, str] = {}
        # Short CPU window for *every* process (sustained-CPU detection), ~15 minutes.
        self.cpu_maxlen = max(12, int(15 * 60 / process_interval_s))
        self.recent_cpu: dict[tuple[int, float], deque] = {}

    # ----------------------------------------------------------------- write
    def add_system(self, point: SystemPoint) -> None:
        with self._lock:
            self.system.append(point)

    def add_system_snapshot(self, snap: SystemSnapshot, fullscreen: bool = False) -> None:
        self.add_system(
            SystemPoint(
                t=snap.timestamp, cpu=snap.cpu.total_percent, ram_percent=snap.memory.percent,
                available=snap.memory.available, commit_percent=snap.memory.commit_percent,
                disk_read_bps=snap.disk_activity.read_bps, disk_write_bps=snap.disk_activity.write_bps,
                disk_busy=snap.disk_activity.busy_percent, process_count=snap.process_count,
                user_idle=snap.user_idle_seconds,
                interrupt_dpc=snap.cpu.interrupt_percent + snap.cpu.dpc_percent,
                hard_faults=snap.memory.hard_faults_per_sec, fullscreen=fullscreen,
            )
        )

    def add_processes(self, t: float, processes: list[ProcessInfo]) -> None:
        with self._lock:
            live: set[tuple[int, float]] = set()
            groups: dict[str, list[float]] = {}
            for p in processes:
                lname = p.name.lower()
                g = groups.setdefault(lname, [0.0, 0.0, 0])
                g[0] += p.private
                g[1] += p.cpu_percent
                g[2] += 1
                self.group_display.setdefault(lname, p.name)
                key = p.key
                live.add(key)
                self.recent_cpu.setdefault(key, deque(maxlen=self.cpu_maxlen)).append((t, p.cpu_percent))
                series = self.instances.get(key)
                if series is None:
                    if p.private < self.track_threshold:
                        continue
                    series = InstanceSeries(p.name, p.pid, p.create_time, deque(maxlen=self.proc_maxlen), exe=p.exe)
                    self.instances[key] = series
                series.points.append((t, p.private, p.cpu_percent, p.rss))
                series.last_seen = t
            for lname, (priv, cpu, count) in groups.items():
                self.groups.setdefault(lname, deque(maxlen=self.proc_maxlen)).append((t, priv, cpu, count))
            # Forget exited processes.
            for key in [k for k in self.instances if k not in live]:
                del self.instances[key]
            for key in [k for k in self.recent_cpu if k not in live]:
                del self.recent_cpu[key]
            for lname in [n for n in self.groups if n not in groups]:
                del self.groups[lname]

    def warm_start(self, rows) -> None:
        """Seed instance series from persisted samples (so leak tracking survives restarts)."""
        with self._lock:
            for r in rows:
                key = (r.pid, round(r.create_time, 3))
                series = self.instances.get(key)
                if series is None:
                    series = InstanceSeries(r.process_name, r.pid, r.create_time, deque(maxlen=self.proc_maxlen))
                    self.instances[key] = series
                series.points.append((r.timestamp, r.memory_bytes, r.cpu_percent or 0.0, r.working_set or 0))
                series.last_seen = r.timestamp

    def prune_to_live(self, live_keys: set[tuple[int, float]]) -> None:
        with self._lock:
            for key in [k for k in self.instances if k not in live_keys]:
                del self.instances[key]

    # ------------------------------------------------------------------ read
    def system_points(self, since: float | None = None) -> list[SystemPoint]:
        with self._lock:
            if since is None:
                return list(self.system)
            return [p for p in self.system if p.t >= since]

    def instance_series(self) -> list[InstanceSeries]:
        with self._lock:
            return [
                InstanceSeries(s.name, s.pid, s.create_time, deque(s.points), s.last_seen, s.exe)
                for s in self.instances.values()
            ]

    def group_series(self, lname: str) -> list[tuple[float, float, float, int]]:
        with self._lock:
            return list(self.groups.get(lname.lower(), ()))

    def cpu_window(self, key: tuple[int, float], since: float) -> list[float]:
        with self._lock:
            return [c for t, c in self.recent_cpu.get(key, ()) if t >= since]

    def cpu_coverage_seconds(self, key: tuple[int, float]) -> float:
        with self._lock:
            series = self.recent_cpu.get(key)
            return series[-1][0] - series[0][0] if series and len(series) > 1 else 0.0

    def group_names(self) -> list[str]:
        with self._lock:
            return list(self.groups)

    def display_name(self, lname: str) -> str:
        return self.group_display.get(lname, lname)

    def clear(self) -> None:
        with self._lock:
            self.system.clear()
            self.instances.clear()
            self.groups.clear()
            self.recent_cpu.clear()
