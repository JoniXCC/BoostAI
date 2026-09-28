"""Plain data models for measured system state.

These are lightweight dataclasses because they are created on the hot sampling path.
Nothing here performs I/O.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum

KB = 1024
_HASH_SUFFIX = re.compile(r"_[0-9A-Fa-f]{16,}$")
MB = 1024**2
GB = 1024**3


def fmt_bytes(value: float | None) -> str:
    if value is None:
        return "n/a"
    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    for unit, size in (("GB", GB), ("MB", MB), ("KB", KB)):
        if value >= size:
            return f"{sign}{value / size:.1f} {unit}" if unit == "GB" else f"{sign}{value / size:.0f} {unit}"
    return f"{sign}{value:.0f} B"


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes} min"


class PressureLevel(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(slots=True)
class GpuInfo:
    name: str
    memory_bytes: int | None = None
    driver_version: str | None = None


@dataclass(slots=True)
class StorageDevice:
    name: str
    media_type: str = "Unknown"   # SSD / HDD / SCM / Unknown
    bus_type: str = "Unknown"
    health: str = "Unknown"       # Healthy / Warning / Unhealthy / Unknown
    size_bytes: int | None = None


@dataclass(slots=True)
class SystemInfo:
    os_name: str
    os_version: str
    os_build: str
    architecture: str
    hostname: str
    cpu_model: str
    physical_cores: int | None
    logical_cores: int
    total_ram: int
    boot_time: float
    is_admin: bool
    gpus: list[GpuInfo] = field(default_factory=list)
    storage: list[StorageDevice] = field(default_factory=list)

    @property
    def uptime_seconds(self) -> float:
        return max(0.0, time.time() - self.boot_time)


@dataclass(slots=True)
class CpuSnapshot:
    total_percent: float
    per_core: list[float]
    interrupt_percent: float = 0.0
    dpc_percent: float = 0.0
    frequency_mhz: float | None = None


@dataclass(slots=True)
class MemorySnapshot:
    total: int
    available: int
    used: int
    percent: float
    cached: int | None = None
    commit_total: int | None = None
    commit_limit: int | None = None
    pagefile_total: int | None = None
    pagefile_used: int | None = None
    hard_faults_per_sec: float | None = None
    compressed_bytes: int | None = None

    @property
    def commit_percent(self) -> float | None:
        if not self.commit_total or not self.commit_limit:
            return None
        return 100.0 * self.commit_total / self.commit_limit


@dataclass(slots=True)
class DiskVolume:
    mountpoint: str
    device: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float
    is_system: bool = False


@dataclass(slots=True)
class DiskActivity:
    read_bps: float = 0.0
    write_bps: float = 0.0
    busy_percent: float | None = None
    avg_latency_ms: float | None = None
    queue_length: float | None = None


@dataclass(slots=True)
class ProcessInfo:
    pid: int
    name: str
    create_time: float
    rss: int                 # working set
    private: int             # private bytes (commit charge) - the better leak signal on Windows
    cpu_percent: float       # normalised to total machine capacity (0-100)
    threads: int = 0
    handles: int | None = None
    ppid: int | None = None
    exe: str | None = None
    username: str | None = None
    status: str = "running"
    has_window: bool = False
    is_foreground: bool = False
    io_bps: float = 0.0      # disk + network + other I/O bytes/s
    session_id: int = 0      # 0 = services session

    @property
    def key(self) -> tuple[int, float]:
        """Identity that survives PID reuse."""
        return (self.pid, round(self.create_time, 3))


@dataclass(slots=True)
class ProcessGroup:
    """All instances of one executable name (e.g. every chrome.exe)."""

    name: str
    count: int
    rss: int
    private: int
    cpu_percent: float
    pids: list[int]
    has_window: bool
    is_foreground: bool


def group_processes(processes: list[ProcessInfo]) -> list[ProcessGroup]:
    groups: dict[str, ProcessGroup] = {}
    for p in processes:
        key = p.name.lower()
        g = groups.get(key)
        if g is None:
            groups[key] = ProcessGroup(p.name, 1, p.rss, p.private, p.cpu_percent, [p.pid], p.has_window, p.is_foreground)
        else:
            g.count += 1
            g.rss += p.rss
            g.private += p.private
            g.cpu_percent += p.cpu_percent
            g.pids.append(p.pid)
            g.has_window = g.has_window or p.has_window
            g.is_foreground = g.is_foreground or p.is_foreground
    return sorted(groups.values(), key=lambda g: g.rss, reverse=True)


@dataclass(slots=True)
class TemperatureReading:
    sensor: str
    celsius: float
    source: str


@dataclass(slots=True)
class TemperatureStatus:
    readings: list[TemperatureReading] = field(default_factory=list)
    cpu_available: bool = False
    gpu_available: bool = False
    message: str = "Temperature monitoring unavailable on this system."


@dataclass(slots=True)
class PowerPlan:
    guid: str
    name: str
    active: bool = False


class StartupSource(str, Enum):
    HKCU_RUN = "HKCU_RUN"
    HKLM_RUN = "HKLM_RUN"
    HKLM_RUN32 = "HKLM_RUN32"
    USER_FOLDER = "USER_FOLDER"
    COMMON_FOLDER = "COMMON_FOLDER"

    @property
    def requires_admin(self) -> bool:
        return self in (StartupSource.HKLM_RUN, StartupSource.HKLM_RUN32, StartupSource.COMMON_FOLDER)

    @property
    def label(self) -> str:
        return {
            "HKCU_RUN": "Registry (current user)",
            "HKLM_RUN": "Registry (all users)",
            "HKLM_RUN32": "Registry (all users, 32-bit)",
            "USER_FOLDER": "Startup folder (current user)",
            "COMMON_FOLDER": "Startup folder (all users)",
        }[self.value]


@dataclass(slots=True)
class StartupItem:
    item_id: str
    name: str
    command: str
    source: StartupSource
    enabled: bool
    target_path: str | None = None
    publisher: str | None = None

    @property
    def requires_admin(self) -> bool:
        return self.source.requires_admin

    @property
    def display_name(self) -> str:
        """Human-friendly name (strips per-install hash suffixes such as Edge's auto-launch entry)."""
        name = _HASH_SUFFIX.sub("", self.name)
        if name.lower() == "microsoftedgeautolaunch":
            return "Microsoft Edge (auto-launch)"
        return name

    @property
    def executable_name(self) -> str | None:
        if not self.target_path:
            return None
        return self.target_path.replace("/", "\\").rsplit("\\", 1)[-1].lower()


@dataclass(slots=True)
class ServiceInfo:
    name: str
    display_name: str
    status: str
    start_type: str
    pid: int | None = None
    username: str | None = None
    binpath: str | None = None


@dataclass(slots=True)
class SystemSnapshot:
    """A point-in-time measurement of the machine."""

    timestamp: float
    cpu: CpuSnapshot
    memory: MemorySnapshot
    memory_pressure: PressureLevel
    disks: list[DiskVolume]
    disk_activity: DiskActivity
    processes: list[ProcessInfo]
    temperatures: TemperatureStatus
    power_plan: PowerPlan | None = None
    user_idle_seconds: float | None = None

    @property
    def process_count(self) -> int:
        return len(self.processes)

    @property
    def system_disk(self) -> DiskVolume | None:
        return next((d for d in self.disks if d.is_system), self.disks[0] if self.disks else None)
