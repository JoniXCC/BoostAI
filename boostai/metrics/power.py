"""Windows power plans via the documented powrprof.dll API (no powercfg.exe shell-out)."""

from __future__ import annotations

import ctypes
import logging
import uuid
from ctypes import wintypes

from boostai.core.models import PowerPlan
from boostai.utils.winapi import IS_WINDOWS

log = logging.getLogger(__name__)

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
POWER_SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"
ULTIMATE_PERFORMANCE = "e9a42b02-d5df-448d-aa00-03f14749eb61"

ACCESS_SCHEME = 16
ERROR_NO_MORE_ITEMS = 259


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, value: str) -> "GUID":
        u = uuid.UUID(value)
        g = cls()
        ctypes.memmove(ctypes.byref(g), u.bytes_le, 16)
        return g

    def to_str(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(ctypes.string_at(ctypes.byref(self), 16))))


def _powrprof():
    return ctypes.WinDLL("powrprof")


def _friendly_name(lib, guid: GUID) -> str:
    size = wintypes.DWORD(0)
    lib.PowerReadFriendlyName(None, ctypes.byref(guid), None, None, None, ctypes.byref(size))
    if size.value == 0:
        return guid.to_str()
    buf = ctypes.create_string_buffer(size.value)
    if lib.PowerReadFriendlyName(None, ctypes.byref(guid), None, None, buf, ctypes.byref(size)) != 0:
        return guid.to_str()
    return ctypes.wstring_at(buf).strip() or guid.to_str()


def active_scheme_guid() -> str | None:
    if not IS_WINDOWS:
        return None
    lib = _powrprof()
    ptr = ctypes.POINTER(GUID)()
    if lib.PowerGetActiveScheme(None, ctypes.byref(ptr)) != 0:
        return None
    try:
        return ptr.contents.to_str()
    finally:
        ctypes.windll.kernel32.LocalFree(ptr)


def list_power_plans() -> list[PowerPlan]:
    if not IS_WINDOWS:
        return []
    try:
        lib = _powrprof()
        active = active_scheme_guid()
        plans: list[PowerPlan] = []
        index = 0
        while True:
            guid = GUID()
            size = wintypes.DWORD(ctypes.sizeof(guid))
            rc = lib.PowerEnumerate(None, None, None, ACCESS_SCHEME, index, ctypes.byref(guid), ctypes.byref(size))
            if rc == ERROR_NO_MORE_ITEMS or rc != 0:
                break
            gid = guid.to_str()
            plans.append(PowerPlan(guid=gid, name=_friendly_name(lib, guid), active=gid == active))
            index += 1
        return plans
    except OSError as exc:
        log.info("Power plan enumeration failed: %s", exc)
        return []


def active_power_plan() -> PowerPlan | None:
    return next((p for p in list_power_plans() if p.active), None)


def set_active_scheme(guid: str) -> bool:
    """Activate an *existing* power scheme. Callers must validate the GUID first."""
    if not IS_WINDOWS:
        return False
    lib = _powrprof()
    return lib.PowerSetActiveScheme(None, ctypes.byref(GUID.from_str(guid))) == 0
