"""Single-call process snapshot via ``NtQuerySystemInformation(SystemProcessInformation)``.

Why: psutil falls back to a full system snapshot *per attribute* for processes the
current user cannot open (services, protected processes). With ~350 processes that
made one collection take >10 s. One snapshot here returns every process's memory,
CPU times, thread/handle counts, parent PID and I/O counters in a few milliseconds,
without opening any process handle. This is the same data source Task Manager uses.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from boostai.utils.winapi import IS_WINDOWS

SystemProcessInformation = 5
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
EPOCH_DIFF_100NS = 116444736000000000
THREAD_STATE_WAITING = 5
WAIT_REASON_SUSPENDED = 5


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


class SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", UNICODE_STRING),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wintypes.ULONG),
        ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
        ("ReadOperationCount", ctypes.c_longlong),
        ("WriteOperationCount", ctypes.c_longlong),
        ("OtherOperationCount", ctypes.c_longlong),
        ("ReadTransferCount", ctypes.c_longlong),
        ("WriteTransferCount", ctypes.c_longlong),
        ("OtherTransferCount", ctypes.c_longlong),
    ]


class CLIENT_ID(ctypes.Structure):
    _fields_ = [("UniqueProcess", ctypes.c_void_p), ("UniqueThread", ctypes.c_void_p)]


class SYSTEM_THREAD_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("KernelTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("CreateTime", ctypes.c_longlong),
        ("WaitTime", wintypes.ULONG),
        ("StartAddress", ctypes.c_void_p),
        ("ClientId", CLIENT_ID),
        ("Priority", ctypes.c_long),
        ("BasePriority", ctypes.c_long),
        ("ContextSwitches", wintypes.ULONG),
        ("ThreadState", wintypes.ULONG),
        ("WaitReason", wintypes.ULONG),
    ]


@dataclass(slots=True)
class RawProcess:
    pid: int
    ppid: int
    name: str
    create_time: float       # unix seconds
    cpu_seconds: float       # user + kernel
    threads: int
    handles: int
    working_set: int
    private_bytes: int
    session_id: int
    io_read_bytes: int
    io_write_bytes: int
    suspended: bool


def snapshot() -> list[RawProcess] | None:
    """Return all processes, or ``None`` if the API is unavailable (caller falls back to psutil)."""
    if not IS_WINDOWS:
        return None
    global _buffer
    ntdll = ctypes.WinDLL("ntdll")
    for _ in range(8):
        # Reuse one buffer between calls (the monitor calls this every few seconds).
        if _buffer is None:
            _buffer = ctypes.create_string_buffer(1 << 21)
        size = len(_buffer)
        needed = wintypes.ULONG(0)
        status = ntdll.NtQuerySystemInformation(SystemProcessInformation, _buffer, size, ctypes.byref(needed)) & 0xFFFFFFFF
        if status == STATUS_INFO_LENGTH_MISMATCH:
            _buffer = ctypes.create_string_buffer(max(size * 2, int(needed.value * 1.25) + 65536))
            continue
        if status != 0:
            return None
        return _parse(_buffer)
    return None


_buffer = None


def _parse(buf) -> list[RawProcess]:
    base = ctypes.addressof(buf)
    proc_size = ctypes.sizeof(SYSTEM_PROCESS_INFORMATION)
    thread_size = ctypes.sizeof(SYSTEM_THREAD_INFORMATION)
    result: list[RawProcess] = []
    offset = 0
    while True:
        info = SYSTEM_PROCESS_INFORMATION.from_address(base + offset)
        pid = int(info.UniqueProcessId or 0)
        if info.ImageName.Buffer and info.ImageName.Length:
            name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2)
        else:
            name = "System Idle Process" if pid == 0 else f"pid-{pid}"

        suspended = False
        n_threads = int(info.NumberOfThreads)
        if n_threads:
            suspended = True
            thread_base = base + offset + proc_size
            for i in range(n_threads):
                t = SYSTEM_THREAD_INFORMATION.from_address(thread_base + i * thread_size)
                if not (t.ThreadState == THREAD_STATE_WAITING and t.WaitReason == WAIT_REASON_SUSPENDED):
                    suspended = False
                    break

        result.append(
            RawProcess(
                pid=pid,
                ppid=int(info.InheritedFromUniqueProcessId or 0),
                name=name,
                create_time=(info.CreateTime - EPOCH_DIFF_100NS) / 1e7 if info.CreateTime else 0.0,
                cpu_seconds=(info.UserTime + info.KernelTime) / 1e7,
                threads=n_threads,
                handles=int(info.HandleCount),
                working_set=int(info.WorkingSetSize),
                private_bytes=int(info.PagefileUsage),
                session_id=int(info.SessionId),
                io_read_bytes=int(info.ReadTransferCount),
                io_write_bytes=int(info.WriteTransferCount),
                suspended=suspended,
            )
        )
        if info.NextEntryOffset == 0:
            break
        offset += info.NextEntryOffset
    return result
