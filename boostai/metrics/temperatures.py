"""Temperature readings from *reliable* sources only.

* NVIDIA GPUs: NVML (``nvml.dll``, installed with the NVIDIA driver) - the same library
  nvidia-smi uses.
* CPU / other sensors: only if LibreHardwareMonitor or OpenHardwareMonitor is running and
  publishing its documented WMI namespace.

ACPI "thermal zone" values are deliberately *not* used: on most laptops they are static
or unrelated to the CPU die, and showing them would be inventing data.
If nothing reliable is available, BoostAI says so instead of showing a number.

Temperatures are read on demand (scans, gaming check) rather than in the background
loop, because querying a sleeping discrete laptop GPU can wake it up.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading

from boostai.core.models import TemperatureReading, TemperatureStatus

log = logging.getLogger(__name__)

_NVML_PATHS = [
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll"),
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll",
]


class _Nvml:
    def __init__(self) -> None:
        self.lib = None
        for path in _NVML_PATHS:
            if os.path.exists(path):
                try:
                    lib = ctypes.CDLL(path)
                    if lib.nvmlInit_v2() == 0:
                        self.lib = lib
                        break
                except OSError:
                    continue

    def read(self) -> list[TemperatureReading]:
        if self.lib is None:
            return []
        readings: list[TemperatureReading] = []
        count = ctypes.c_uint(0)
        if self.lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            return readings
        for i in range(count.value):
            handle = ctypes.c_void_p()
            if self.lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(handle)) != 0:
                continue
            name = ctypes.create_string_buffer(96)
            self.lib.nvmlDeviceGetName(handle, name, 96)
            temp = ctypes.c_uint(0)
            if self.lib.nvmlDeviceGetTemperature(handle, 0, ctypes.byref(temp)) == 0:  # NVML_TEMPERATURE_GPU
                label = name.value.decode(errors="replace") or f"NVIDIA GPU {i}"
                readings.append(TemperatureReading(sensor=label, celsius=float(temp.value), source="NVML"))
        return readings

    def close(self) -> None:
        if self.lib is not None:
            try:
                self.lib.nvmlShutdown()
            except OSError:
                pass
            self.lib = None


def _hardware_monitor_wmi() -> list[TemperatureReading]:
    from boostai.utils.wmi import wmi_query

    readings: list[TemperatureReading] = []
    for namespace in ("LibreHardwareMonitor", "OpenHardwareMonitor"):
        try:
            rows = wmi_query(
                rf"root\{namespace}",
                "SELECT Name, Value, Identifier FROM Sensor WHERE SensorType='Temperature'",
                ["Name", "Value", "Identifier"],
            )
        except Exception:
            continue  # namespace absent: the hardware monitor is not running
        for row in rows:
            ident = str(row["Identifier"] or "").lower()
            kind = "CPU" if "cpu" in ident else "GPU" if "gpu" in ident else "Other"
            readings.append(TemperatureReading(sensor=f"{kind}: {row['Name']}", celsius=float(row["Value"]), source=namespace))
        if readings:
            break
    return readings


class TemperatureReader:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nvml: _Nvml | None = None

    def read(self) -> TemperatureStatus:
        with self._lock:
            if self._nvml is None:
                self._nvml = _Nvml()
            readings = []
            try:
                readings += self._nvml.read()
            except OSError as exc:
                log.info("NVML read failed: %s", exc)
            readings += _hardware_monitor_wmi()
        cpu = any(r.sensor.startswith("CPU") for r in readings)
        gpu = any(r.source == "NVML" or r.sensor.startswith("GPU") for r in readings)
        if cpu and gpu:
            message = "CPU and GPU temperatures available."
        elif gpu:
            message = (
                "GPU temperature available (NVIDIA NVML). CPU temperature unavailable: Windows does not expose "
                "a reliable CPU sensor without a hardware-monitor driver (e.g. LibreHardwareMonitor)."
            )
        elif cpu:
            message = "CPU temperature available via hardware monitor."
        else:
            message = "Temperature monitoring unavailable on this system."
        return TemperatureStatus(readings=readings, cpu_available=cpu, gpu_available=gpu, message=message)

    def close(self) -> None:
        with self._lock:
            if self._nvml is not None:
                self._nvml.close()
