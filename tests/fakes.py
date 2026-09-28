"""Test doubles. FakeSystemOps records calls instead of touching the real OS."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from boostai.core.models import (
    CpuSnapshot,
    DiskActivity,
    DiskVolume,
    GB,
    MB,
    MemorySnapshot,
    PowerPlan,
    PressureLevel,
    ProcessInfo,
    ServiceInfo,
    StartupItem,
    StartupSource,
    SystemInfo,
    SystemSnapshot,
    TemperatureStatus,
)

USER = "TESTPC\\alice"


def proc(pid: int = 1000, name: str = "app.exe", *, private: int = 200 * MB, rss: int | None = None,
         cpu: float = 0.0, user: str | None = USER, exe: str | None = None, session: int = 1,
         create_time: float = 1_700_000_000.0, ppid: int = 1, window: bool = True,
         foreground: bool = False) -> ProcessInfo:
    return ProcessInfo(
        pid=pid, name=name, create_time=create_time, rss=rss if rss is not None else private, private=private,
        cpu_percent=cpu, threads=10, handles=100, ppid=ppid,
        exe=exe if exe is not None else f"C:\\Program Files\\Test\\{name}", username=user, status="running",
        has_window=window, is_foreground=foreground, session_id=session,
    )


def snapshot(processes: list[ProcessInfo] | None = None, *, ram_percent: float = 50.0, total: int = 16 * GB,
             commit: float = 60.0, pressure: PressureLevel = PressureLevel.NORMAL, cpu: float = 10.0,
             free_percent: float = 50.0, t: float | None = None) -> SystemSnapshot:
    used = int(total * ram_percent / 100)
    mem = MemorySnapshot(total=total, available=total - used, used=used, percent=ram_percent,
                         commit_total=int(commit * 10 * GB / 100), commit_limit=10 * GB,
                         pagefile_total=4 * GB, pagefile_used=1 * GB)
    disk_total = 500 * GB
    free = int(disk_total * free_percent / 100)
    return SystemSnapshot(
        timestamp=t or time.time(), cpu=CpuSnapshot(cpu, [cpu] * 8), memory=mem, memory_pressure=pressure,
        disks=[DiskVolume("C:\\", "C:\\", "NTFS", disk_total, disk_total - free, free, 100 - free_percent, True)],
        disk_activity=DiskActivity(0, 0, 5.0, 1.0, 0.0), processes=processes or [],
        temperatures=TemperatureStatus(),
    )


def system_info(total: int = 16 * GB) -> SystemInfo:
    return SystemInfo("Windows 11 Pro", "24H2", "26100.1", "AMD64", "TESTPC", "Test CPU", 8, 16, total,
                      time.time() - 3600, False)


@dataclass
class FakeSystemOps:
    processes: list[ProcessInfo] = field(default_factory=list)
    service_pid_set: set[int] = field(default_factory=set)
    startup_items: list[StartupItem] = field(default_factory=list)
    startup_state: dict[tuple[str, str], bytes | None] = field(default_factory=dict)
    plans: list[PowerPlan] = field(default_factory=list)
    services: dict[str, ServiceInfo] = field(default_factory=dict)
    calls: list[tuple] = field(default_factory=list)
    close_works: bool = True
    available: int = 8 * GB
    free_space: int = 100 * GB
    cmdlines: dict[int, list[str]] = field(default_factory=dict)
    restore_points: list[tuple[int, str]] = field(default_factory=list)

    # processes
    def list_processes(self):
        return list(self.processes)

    def service_pids(self):
        return set(self.service_pid_set)

    def process_cmdline(self, pid):
        return self.cmdlines.get(pid)

    def post_close(self, pid):
        self.calls.append(("post_close", pid))
        if self.close_works:
            self.processes = [p for p in self.processes if p.pid != pid]
            return 1
        return 1

    def terminate(self, pid):
        self.calls.append(("terminate", pid))
        self.processes = [p for p in self.processes if p.pid != pid]
        return True

    def is_running(self, pid, create_time):
        return any(p.pid == pid for p in self.processes)

    def launch(self, argv, cwd):
        self.calls.append(("launch", tuple(argv)))
        return True

    def trim_working_set(self, pid):
        self.calls.append(("trim", pid))
        for p in self.processes:
            if p.pid == pid:
                p.rss = p.rss // 4
        return True

    # startup
    def list_startup_items(self):
        for item in self.startup_items:
            raw = self.startup_state.get((item.source.value, item.name))
            item.enabled = not raw or (raw[0] & 1) == 0
        return list(self.startup_items)

    def read_startup_state(self, source: StartupSource, value_name):
        return self.startup_state.get((source.value, value_name))

    def write_startup_state(self, source: StartupSource, value_name, data):
        self.calls.append(("write_startup", source.value, value_name, data))
        self.startup_state[(source.value, value_name)] = data

    # power / network
    def list_power_plans(self):
        return [PowerPlan(p.guid, p.name, p.active) for p in self.plans]

    def set_power_plan(self, guid):
        self.calls.append(("set_plan", guid))
        for p in self.plans:
            p.active = p.guid == guid
        return True

    def flush_dns(self):
        self.calls.append(("flush_dns",))
        return True

    # services
    def get_service(self, name):
        return self.services.get(name.lower())

    def stop_service(self, name, timeout=30.0):
        self.calls.append(("stop_service", name))
        self.services[name.lower()].status = "stopped"
        return True

    def start_service(self, name, timeout=30.0):
        self.calls.append(("start_service", name))
        self.services[name.lower()].status = "running"
        return True

    # files / system
    def memory_available(self):
        return self.available

    def delete_file(self, path):
        import os

        self.calls.append(("delete", path))
        try:
            os.remove(path)
            return True
        except OSError:
            return False

    def remove_empty_dir(self, path):
        import os

        try:
            os.rmdir(path)
            return True
        except OSError:
            return False

    def disk_free(self, path):
        return self.free_space

    def create_restore_point(self, description):
        self.calls.append(("restore_point", description))
        seq = (self.restore_points[-1][0] + 1) if self.restore_points else 1
        self.restore_points.append((seq, description))
        return True

    def latest_restore_point(self):
        return self.restore_points[-1] if self.restore_points else None
