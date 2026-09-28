"""Startup program discovery and the Task-Manager-compatible enable/disable mechanism.

BoostAI never deletes startup entries. Enabling/disabling is done exactly the way the
Windows Task Manager "Startup apps" page does it: by writing a 12-byte state value under
``...\\Explorer\\StartupApproved\\{Run,Run32,StartupFolder}``. The original ``Run`` value
and shortcut files are left untouched, which makes the change fully reversible.
"""

from __future__ import annotations

import logging
import os
import struct
import time
from pathlib import Path

from boostai.core.models import StartupItem, StartupSource
from boostai.utils import winapi

log = logging.getLogger(__name__)

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32_KEY = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
APPROVED_BASE = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

ENABLED_BYTES = bytes([0x02] + [0x00] * 11)


def _hive(source: StartupSource):
    return winreg.HKEY_CURRENT_USER if source in (StartupSource.HKCU_RUN, StartupSource.USER_FOLDER) else winreg.HKEY_LOCAL_MACHINE


def approved_location(source: StartupSource) -> tuple[object, str]:
    """(hive, subkey) of the StartupApproved key for a source."""
    sub = {
        StartupSource.HKCU_RUN: "Run",
        StartupSource.HKLM_RUN: "Run",
        StartupSource.HKLM_RUN32: "Run32",
        StartupSource.USER_FOLDER: "StartupFolder",
        StartupSource.COMMON_FOLDER: "StartupFolder",
    }[source]
    return _hive(source), f"{APPROVED_BASE}\\{sub}"


def disabled_bytes(now: float | None = None) -> bytes:
    """0x03 marker followed by a FILETIME of when it was disabled (as Task Manager writes it)."""
    ts = now if now is not None else time.time()
    filetime = int((ts + 11644473600) * 10_000_000)
    return bytes([0x03, 0x00, 0x00, 0x00]) + struct.pack("<Q", filetime)


def is_enabled_state(raw: bytes | None) -> bool:
    """No value means enabled; otherwise the low bit of the first byte marks 'disabled'."""
    if not raw:
        return True
    return (raw[0] & 0x01) == 0


def read_approved(source: StartupSource, value_name: str) -> bytes | None:
    if winreg is None:
        return None
    hive, sub = approved_location(source)
    try:
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ) as key:
            data, _ = winreg.QueryValueEx(key, value_name)
            return bytes(data) if data is not None else None
    except OSError:
        return None


def write_approved(source: StartupSource, value_name: str, data: bytes | None) -> None:
    """Write (or, with ``None``, remove) the StartupApproved marker. Raises PermissionError for HKLM without admin."""
    hive, sub = approved_location(source)
    if data is None:
        try:
            with winreg.OpenKey(hive, sub, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, value_name)
        except FileNotFoundError:
            pass
        return
    with winreg.CreateKeyEx(hive, sub, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, data)


def extract_target(command: str) -> str | None:
    """Best-effort executable path from a Run command line (never executed, only inspected)."""
    cmd = os.path.expandvars(command.strip())
    if not cmd:
        return None
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 1 else cmd.strip('"')
    lower = cmd.lower()
    for ext in (".exe", ".bat", ".cmd", ".com"):
        idx = lower.find(ext)
        if idx != -1:
            return cmd[: idx + len(ext)]
    return cmd.split(" ", 1)[0]


def _resolve_shortcut(path: Path) -> str | None:
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            link = shell.CreateShortcut(str(path))
            target = str(link.TargetPath) or None
            del link, shell  # release COM objects before CoUninitialize
            return target
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return None


def _registry_items(source: StartupSource, key_path: str) -> list[StartupItem]:
    items: list[StartupItem] = []
    try:
        with winreg.OpenKey(_hive(source), key_path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if not name or not isinstance(value, str):
                    continue
                target = extract_target(value)
                items.append(
                    StartupItem(
                        item_id=f"{source.value}:{name}",
                        name=name,
                        command=value,
                        source=source,
                        enabled=is_enabled_state(read_approved(source, name)),
                        target_path=target,
                        publisher=winapi.file_company_name(target) if target else None,
                    )
                )
    except OSError:
        pass
    return items


def startup_folders() -> dict[StartupSource, Path]:
    appdata = os.environ.get("APPDATA", "")
    programdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return {
        StartupSource.USER_FOLDER: Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup",
        StartupSource.COMMON_FOLDER: Path(programdata) / "Microsoft/Windows/Start Menu/Programs/StartUp",
    }


def _folder_items(source: StartupSource, folder: Path) -> list[StartupItem]:
    items: list[StartupItem] = []
    if not folder.is_dir():
        return items
    for entry in folder.iterdir():
        if entry.name.lower() == "desktop.ini" or not entry.is_file():
            continue
        target = _resolve_shortcut(entry) if entry.suffix.lower() == ".lnk" else str(entry)
        items.append(
            StartupItem(
                item_id=f"{source.value}:{entry.name}",
                name=entry.stem,
                command=str(entry),
                source=source,
                enabled=is_enabled_state(read_approved(source, entry.name)),
                target_path=target,
                publisher=winapi.file_company_name(target) if target else None,
            )
        )
    return items


def approved_value_name(item: StartupItem) -> str:
    """Name used under StartupApproved: the Run value name, or the file name for folder items."""
    if item.source in (StartupSource.USER_FOLDER, StartupSource.COMMON_FOLDER):
        return Path(item.command).name
    return item.name


def collect_startup_items() -> list[StartupItem]:
    if winreg is None:
        return []
    items: list[StartupItem] = []
    items += _registry_items(StartupSource.HKCU_RUN, RUN_KEY)
    items += _registry_items(StartupSource.HKLM_RUN, RUN_KEY)
    items += _registry_items(StartupSource.HKLM_RUN32, RUN32_KEY)
    for source, folder in startup_folders().items():
        items += _folder_items(source, folder)
    return items
