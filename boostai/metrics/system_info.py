"""Static machine description (OS, CPU, RAM, GPU, storage). Collected once per session."""

from __future__ import annotations

import logging
import platform
import socket

import psutil

from boostai.core.models import GpuInfo, SystemInfo
from boostai.metrics.disk import collect_storage_devices
from boostai.utils import winapi

log = logging.getLogger(__name__)

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _reg_value(path: str, name: str, hive=None):
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(hive or winreg.HKEY_LOCAL_MACHINE, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def os_description() -> tuple[str, str, str]:
    """(name, display version, build). Windows 11 still reports ProductName 'Windows 10', so use the build."""
    cv = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    build = str(_reg_value(cv, "CurrentBuild") or platform.version().split(".")[-1])
    ubr = _reg_value(cv, "UBR")
    edition = str(_reg_value(cv, "EditionID") or "")
    display = str(_reg_value(cv, "DisplayVersion") or _reg_value(cv, "ReleaseId") or "")
    try:
        major = "Windows 11" if int(build) >= 22000 else "Windows 10"
    except ValueError:
        major = platform.system()
    name = f"{major} {edition}".strip()
    return name, display, f"{build}.{ubr}" if ubr is not None else build


def cpu_model() -> str:
    value = _reg_value(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    return str(value).strip() if value else (platform.processor() or "Unknown CPU")


def collect_gpus() -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    if winreg is None:
        return gpus
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS) as cls:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, index)
                except OSError:
                    break
                index += 1
                if not sub.isdigit():
                    continue
                path = f"{_DISPLAY_CLASS}\\{sub}"
                desc = _reg_value(path, "DriverDesc")
                if not desc:
                    continue
                mem = _reg_value(path, "HardwareInformation.qwMemorySize") or _reg_value(path, "HardwareInformation.MemorySize")
                if isinstance(mem, bytes):
                    mem = int.from_bytes(mem[:8], "little")
                gpus.append(
                    GpuInfo(
                        name=str(desc),
                        memory_bytes=int(mem) if isinstance(mem, int) and mem > 0 else None,
                        driver_version=_reg_value(path, "DriverVersion"),
                    )
                )
    except OSError as exc:
        log.info("GPU enumeration failed: %s", exc)
    # Remove virtual display adapters that only add noise.
    return [g for g in gpus if "basic display" not in g.name.lower() and "virtual" not in g.name.lower()]


def collect_system_info(include_storage: bool = True) -> SystemInfo:
    name, display, build = os_description()
    return SystemInfo(
        os_name=name,
        os_version=display,
        os_build=build,
        architecture=platform.machine(),
        hostname=socket.gethostname(),
        cpu_model=cpu_model(),
        physical_cores=psutil.cpu_count(logical=False),
        logical_cores=psutil.cpu_count(logical=True) or 1,
        total_ram=int(psutil.virtual_memory().total),
        boot_time=float(psutil.boot_time()),
        is_admin=winapi.is_admin(),
        gpus=collect_gpus(),
        storage=collect_storage_devices() if include_storage else [],
    )
