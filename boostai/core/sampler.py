"""Builds :class:`SystemSnapshot` objects from the individual collectors."""

from __future__ import annotations

import threading
import time

from boostai.core.models import SystemSnapshot, TemperatureStatus
from boostai.metrics import disk, memory, power
from boostai.metrics.cpu import CpuSampler
from boostai.metrics.disk import DiskActivitySampler
from boostai.metrics.pdh import PdhSampler
from boostai.metrics.processes import ProcessCollector
from boostai.metrics.temperatures import TemperatureReader
from boostai.utils import winapi


class SystemSampler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cpu = CpuSampler()
        self.pdh = PdhSampler()
        self.disk_activity = DiskActivitySampler()
        self.processes = ProcessCollector()
        self.temperatures = TemperatureReader()
        self._volumes_cache: tuple[float, list] = (0.0, [])

    def volumes(self):
        # Free-space changes slowly; refreshing every 30 s is plenty.
        ts, cached = self._volumes_cache
        if time.time() - ts > 30 or not cached:
            cached = disk.collect_volumes()
            self._volumes_cache = (time.time(), cached)
        return cached

    def snapshot(self, *, include_processes: bool = True, include_temperatures: bool = False,
                 include_power: bool = False) -> SystemSnapshot:
        with self._lock:
            pdh_values = self.pdh.sample()
            cpu = self.cpu.sample()
            processes = self.processes.collect() if include_processes else []
            mem = memory.collect_memory(pdh_values.get("hard_faults_per_sec"), self.processes.compressed_bytes)
            activity = self.disk_activity.sample(pdh_values)
            temps = self.temperatures.read() if include_temperatures else TemperatureStatus(
                message="Temperatures are read during scans.")
            plan = power.active_power_plan() if include_power else None
            return SystemSnapshot(
                timestamp=time.time(),
                cpu=cpu,
                memory=mem,
                memory_pressure=memory.pressure_level(mem),
                disks=self.volumes(),
                disk_activity=activity,
                processes=processes,
                temperatures=temps,
                power_plan=plan,
                user_idle_seconds=winapi.user_idle_seconds(),
            )

    def close(self) -> None:
        self.pdh.close()
        self.temperatures.close()
