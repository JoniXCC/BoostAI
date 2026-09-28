"""Per-process metrics collection.

Design notes
------------
* One ``NtQuerySystemInformation`` snapshot per collection (see ``utils.ntsnapshot``)
  provides memory, CPU time, threads, handles, parent and I/O for *every* process
  without opening process handles. psutil is only used as a fallback.
* A process that denies access or exits mid-read never breaks a scan.
* CPU % is computed from CPU-time deltas keyed by (pid, create time), so PID reuse is
  handled, and is normalised to *total machine capacity* (0-100 %), not per core.
* ``private`` = private bytes / commit charge. This is the most useful leak signal,
  because the working set (``rss``) can be trimmed by Windows at any time.
* Executable path and owner never change for a process instance, so they are cached.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

import psutil

from boostai.core.models import ProcessInfo
from boostai.utils import ntsnapshot, winapi

log = logging.getLogger(__name__)

MEMORY_COMPRESSION_NAMES = {"memcompression", "memory compression"}


@dataclass(slots=True)
class _Static:
    exe: str | None
    username: str | None


class ProcessCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logical = psutil.cpu_count(logical=True) or 1
        self._prev: dict[tuple[int, float], tuple[float, int, float]] = {}  # key -> (cpu_s, io_bytes, t)
        self._static: dict[tuple[int, float], _Static] = {}
        self.self_pid = os.getpid()
        self.compressed_bytes: int | None = None

    def collect(self) -> list[ProcessInfo]:
        with self._lock:
            raw = None
            try:
                raw = ntsnapshot.snapshot()
            except (OSError, ValueError) as exc:
                log.warning("Native process snapshot failed, using psutil: %s", exc)
            if raw is None:
                raw = _psutil_snapshot()
            return self._build(raw)

    def _build(self, raw: list[ntsnapshot.RawProcess]) -> list[ProcessInfo]:
        window_pids = winapi.visible_window_pids()
        fg_pid = winapi.foreground_pid()
        now = time.monotonic()
        seen: set[tuple[int, float]] = set()
        result: list[ProcessInfo] = []
        compressed = None

        for r in raw:
            if r.pid == 0:  # "System Idle Process" is not a real process
                continue
            key = (r.pid, round(r.create_time, 3))
            seen.add(key)
            static = self._static.get(key)
            if static is None:
                static = _Static(exe=winapi.process_image_path(r.pid), username=winapi.process_username(r.pid))
                self._static[key] = static

            io_total = r.io_read_bytes + r.io_write_bytes
            cpu_pct = io_bps = 0.0
            prev = self._prev.get(key)
            if prev is not None and now > prev[2]:
                dt = now - prev[2]
                cpu_pct = max(0.0, (r.cpu_seconds - prev[0]) / dt / self._logical * 100.0)
                io_bps = max(0.0, (io_total - prev[1]) / dt)
            self._prev[key] = (r.cpu_seconds, io_total, now)

            if r.name.lower() in MEMORY_COMPRESSION_NAMES:
                compressed = r.working_set

            result.append(
                ProcessInfo(
                    pid=r.pid,
                    name=r.name,
                    create_time=r.create_time,
                    rss=r.working_set,
                    private=r.private_bytes,
                    cpu_percent=round(min(cpu_pct, 100.0), 2),
                    threads=r.threads,
                    handles=r.handles,
                    ppid=r.ppid,
                    exe=static.exe,
                    username=static.username,
                    status="suspended" if r.suspended else "running",
                    has_window=r.pid in window_pids,
                    is_foreground=r.pid == fg_pid,
                    io_bps=io_bps,
                    session_id=r.session_id,
                )
            )

        # Drop state for processes that have exited so memory use stays bounded.
        for stale in set(self._prev) - seen:
            self._prev.pop(stale, None)
        for stale in set(self._static) - seen:
            self._static.pop(stale, None)
        self.compressed_bytes = compressed
        return result


def _psutil_snapshot() -> list[ntsnapshot.RawProcess]:
    """Portable (slower) fallback."""
    out: list[ntsnapshot.RawProcess] = []
    for p in psutil.process_iter():
        try:
            with p.oneshot():
                mem = p.memory_info()
                t = p.cpu_times()
                out.append(
                    ntsnapshot.RawProcess(
                        pid=p.pid, ppid=p.ppid() or 0, name=p.name(), create_time=p.create_time(),
                        cpu_seconds=t.user + t.system, threads=p.num_threads(),
                        handles=getattr(p, "num_handles", lambda: 0)(), working_set=mem.rss,
                        private_bytes=getattr(mem, "private", mem.vms), session_id=0,
                        io_read_bytes=0, io_write_bytes=0, suspended=False,
                    )
                )
        except (psutil.Error, OSError):
            continue
    return out
