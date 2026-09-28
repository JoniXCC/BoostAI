"""Volumes, disk activity and (when Windows exposes it) physical disk health."""

from __future__ import annotations

import logging
import os
import threading
import time

import psutil

from boostai.core.models import DiskActivity, DiskVolume, StorageDevice

log = logging.getLogger(__name__)

_MEDIA = {3: "HDD", 4: "SSD", 5: "SCM"}
_HEALTH = {0: "Healthy", 1: "Warning", 2: "Unhealthy", 5: "Unknown"}
_BUS = {7: "USB", 8: "RAID", 11: "SATA", 17: "NVMe", 3: "ATA", 10: "SAS"}


def system_drive() -> str:
    return (os.environ.get("SystemDrive") or "C:").rstrip("\\").upper() + "\\"


def collect_volumes() -> list[DiskVolume]:
    sysdrive = system_drive()
    volumes: list[DiskVolume] = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        if "fixed" not in part.opts and part.mountpoint.upper() != sysdrive:
            continue  # removable/network drives are not diagnosed
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        volumes.append(
            DiskVolume(
                mountpoint=part.mountpoint,
                device=part.device,
                fstype=part.fstype,
                total=int(usage.total),
                used=int(usage.used),
                free=int(usage.free),
                percent=float(usage.percent),
                is_system=part.mountpoint.upper() == sysdrive,
            )
        )
    return volumes


class DiskActivitySampler:
    """Read/write throughput from psutil counter deltas, enriched with PDH latency/idle data."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev = self._counters()
        self._prev_time = time.monotonic()

    @staticmethod
    def _counters():
        try:
            return psutil.disk_io_counters(perdisk=False)
        except (OSError, RuntimeError):
            return None

    def sample(self, pdh_values: dict[str, float] | None = None) -> DiskActivity:
        pdh_values = pdh_values or {}
        with self._lock:
            cur, now = self._counters(), time.monotonic()
            elapsed = max(1e-3, now - self._prev_time)
            read_bps = write_bps = 0.0
            if cur is not None and self._prev is not None:
                read_bps = max(0.0, (cur.read_bytes - self._prev.read_bytes) / elapsed)
                write_bps = max(0.0, (cur.write_bytes - self._prev.write_bytes) / elapsed)
            self._prev, self._prev_time = cur, now
        idle = pdh_values.get("disk_idle_percent")
        latency = pdh_values.get("disk_sec_per_transfer")
        return DiskActivity(
            read_bps=read_bps,
            write_bps=write_bps,
            busy_percent=None if idle is None else round(max(0.0, min(100.0, 100.0 - idle)), 1),
            avg_latency_ms=None if latency is None else round(latency * 1000.0, 2),
            queue_length=pdh_values.get("disk_queue_length"),
        )


def collect_storage_devices() -> list[StorageDevice]:
    """Physical disks via the documented Storage Management WMI provider (no admin needed)."""
    try:
        from boostai.utils.wmi import wmi_query

        rows = wmi_query(
            r"root\Microsoft\Windows\Storage",
            "SELECT FriendlyName, MediaType, BusType, HealthStatus, Size FROM MSFT_PhysicalDisk",
            ["FriendlyName", "MediaType", "BusType", "HealthStatus", "Size"],
        )
    except Exception as exc:
        log.info("Physical disk information unavailable: %s", exc)
        return []
    return [
        StorageDevice(
            name=str(row["FriendlyName"] or "Disk"),
            media_type=_MEDIA.get(int(row["MediaType"] or 0), "Unknown"),
            bus_type=_BUS.get(int(row["BusType"] or 0), "Other"),
            health=_HEALTH.get(int(row["HealthStatus"] if row["HealthStatus"] is not None else 5), "Unknown"),
            size_bytes=int(row["Size"]) if row["Size"] else None,
        )
        for row in rows
    ]
