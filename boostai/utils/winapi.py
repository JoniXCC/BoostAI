"""Thin, typed ctypes wrappers around documented Win32 APIs.

No shell commands are used anywhere in BoostAI's system access layer; every OS
interaction is a direct, documented API call with fixed arguments.
All functions degrade gracefully (returning ``None``/empty) on failure.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    # Pointer-sized return types must be declared explicitly on 64-bit Windows.
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetDesktopWindow.restype = wintypes.HWND
    user32.GetShellWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel32.GetTickCount.restype = wintypes.DWORD

PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_CLOSE = 0x0010
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD),
        ("ThreadCount", wintypes.DWORD),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(shell32.IsUserAnAdmin())
    except OSError:
        return False


def get_performance_info() -> dict[str, int] | None:
    """Commit charge, cache and kernel memory in bytes (GetPerformanceInfo)."""
    if not IS_WINDOWS:
        return None
    info = PERFORMANCE_INFORMATION()
    info.cb = ctypes.sizeof(info)
    if not psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
        return None
    page = info.PageSize
    return {
        "commit_total": info.CommitTotal * page,
        "commit_limit": info.CommitLimit * page,
        "commit_peak": info.CommitPeak * page,
        "physical_total": info.PhysicalTotal * page,
        "physical_available": info.PhysicalAvailable * page,
        "system_cache": info.SystemCache * page,
        "kernel_paged": info.KernelPaged * page,
        "kernel_nonpaged": info.KernelNonpaged * page,
        "handle_count": info.HandleCount,
        "process_count": info.ProcessCount,
        "thread_count": info.ThreadCount,
    }


def user_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input in this session."""
    if not IS_WINDOWS:
        return None
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    now = kernel32.GetTickCount() & 0xFFFFFFFF
    return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def foreground_pid() -> int | None:
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    return _window_pid(hwnd) if hwnd else None


_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM) if IS_WINDOWS else None


def _top_level_windows() -> list[int]:
    handles: list[int] = []

    def _cb(hwnd, _lparam):  # noqa: ANN001 - ctypes callback
        handles.append(hwnd)
        return True

    user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return handles


def _is_app_window(hwnd: int) -> bool:
    if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
        return False
    if user32.GetWindowTextLengthW(wintypes.HWND(hwnd)) == 0:
        return False
    ex_style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    return not (ex_style & WS_EX_TOOLWINDOW)


def visible_window_pids() -> set[int]:
    """PIDs that own at least one visible, titled, top-level application window."""
    if not IS_WINDOWS:
        return set()
    try:
        return {_window_pid(h) for h in _top_level_windows() if _is_app_window(h)}
    except OSError:
        return set()


def foreground_is_fullscreen() -> bool:
    """True when the foreground window covers its entire monitor (typical for games/video)."""
    if not IS_WINDOWS:
        return False
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd in (user32.GetDesktopWindow(), user32.GetShellWindow()):
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


def post_close_to_process_windows(pid: int) -> int:
    """Politely ask a process to close by posting WM_CLOSE to its visible windows.

    This is the same message the window's own close button sends, so the application
    can prompt the user to save work. Returns the number of windows messaged.
    """
    if not IS_WINDOWS:
        return 0
    count = 0
    for hwnd in _top_level_windows():
        if _window_pid(hwnd) == pid and user32.IsWindowVisible(wintypes.HWND(hwnd)):
            if user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0):
                count += 1
    return count


def trim_working_set(pid: int) -> bool:
    """Ask Windows to trim a process working set (SetProcessWorkingSetSizeEx(-1, -1)).

    This does not free the process's committed memory; pages are moved to the standby
    list and may be faulted back in. BoostAI labels this action accordingly.
    """
    if not IS_WINDOWS:
        return False
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        minus_one = ctypes.c_size_t(-1 & (2 ** (8 * ctypes.sizeof(ctypes.c_size_t)) - 1))
        kernel32.SetProcessWorkingSetSizeEx.argtypes = [
            wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD
        ]
        return bool(kernel32.SetProcessWorkingSetSizeEx(handle, minus_one, minus_one, 0))
    finally:
        kernel32.CloseHandle(handle)


def process_image_path(pid: int) -> str | None:
    """Full executable path using only PROCESS_QUERY_LIMITED_INFORMATION."""
    if not IS_WINDOWS or pid <= 4:
        return None
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(handle)


_SID_CACHE: dict[str, str] = {}


def process_username(pid: int) -> str | None:
    """DOMAIN\\user owning the process, or None when the token cannot be read."""
    if not IS_WINDOWS or pid <= 0:
        return None
    try:
        import win32api
        import win32con
        import win32security
    except ImportError:
        return None
    try:
        hproc = win32api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except Exception:
        return None
    try:
        htoken = win32security.OpenProcessToken(hproc, win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(htoken, win32security.TokenUser)[0]
        finally:
            htoken.Close()
        key = win32security.ConvertSidToStringSid(sid)
        if key not in _SID_CACHE:
            name, domain, _ = win32security.LookupAccountSid(None, sid)
            _SID_CACHE[key] = f"{domain}\\{name}" if domain else name
        return _SID_CACHE[key]
    except Exception:
        return None
    finally:
        hproc.Close()


def flush_dns_cache() -> bool:
    """DnsFlushResolverCache - same effect as `ipconfig /flushdns`, without spawning a shell."""
    if not IS_WINDOWS:
        return False
    try:
        dnsapi = ctypes.WinDLL("dnsapi")
        return bool(dnsapi.DnsFlushResolverCache())
    except (OSError, AttributeError):
        return False


def file_company_name(path: str) -> str | None:
    """CompanyName from the executable's version resource (used for startup categorisation)."""
    if not IS_WINDOWS or not path:
        return None
    try:
        import win32api

        translations = win32api.GetFileVersionInfo(path, "\\VarFileInfo\\Translation")
        if not translations:
            return None
        lang, codepage = translations[0]
        key = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\CompanyName"
        value = win32api.GetFileVersionInfo(path, key)
        return str(value).strip() or None
    except Exception:  # pywin32 raises pywintypes.error for files without version info
        return None
