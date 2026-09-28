"""Windows Performance Data Helper (PDH) counters.

Counters are added by their *English* names (PdhAddEnglishCounter) so they work on
localised Windows installations. Missing counters are simply skipped.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

DEFAULT_COUNTERS: dict[str, str] = {
    "hard_faults_per_sec": r"\Memory\Pages Input/sec",
    "disk_idle_percent": r"\PhysicalDisk(_Total)\% Idle Time",
    "disk_sec_per_transfer": r"\PhysicalDisk(_Total)\Avg. Disk sec/Transfer",
    "disk_queue_length": r"\PhysicalDisk(_Total)\Current Disk Queue Length",
}


class PdhSampler:
    """Owns one PDH query. Rate counters need two collections, so the first sample is partial."""

    def __init__(self, counters: dict[str, str] | None = None) -> None:
        self._lock = threading.Lock()
        self._query = None
        self._handles: dict[str, object] = {}
        try:
            import win32pdh

            self._pdh = win32pdh
            self._query = win32pdh.OpenQuery()
            for key, path in (counters or DEFAULT_COUNTERS).items():
                try:
                    self._handles[key] = win32pdh.AddEnglishCounter(self._query, path)
                except Exception as exc:  # pywintypes.error
                    log.debug("PDH counter unavailable %s: %s", path, exc)
            win32pdh.CollectQueryData(self._query)
        except Exception as exc:
            log.info("PDH unavailable: %s", exc)
            self._query = None

    @property
    def available(self) -> bool:
        return self._query is not None and bool(self._handles)

    def sample(self) -> dict[str, float]:
        if not self.available:
            return {}
        values: dict[str, float] = {}
        with self._lock:
            try:
                self._pdh.CollectQueryData(self._query)
            except Exception:
                return {}
            for key, handle in self._handles.items():
                try:
                    _, value = self._pdh.GetFormattedCounterValue(handle, self._pdh.PDH_FMT_DOUBLE)
                    values[key] = float(value)
                except Exception:
                    continue  # PDH_INVALID_DATA on the first sample of rate counters
        return values

    def close(self) -> None:
        with self._lock:
            if self._query is not None:
                try:
                    self._pdh.CloseQuery(self._query)
                except Exception:
                    pass
                self._query = None
