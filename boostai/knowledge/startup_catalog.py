"""Startup-item categorisation.

Categories
----------
* PROTECTED        - security software or Windows components. Never offered for disabling.
* RECOMMENDED_KEEP - hardware/driver utilities (audio, touchpad, hotkeys, display).
* OPTIONAL         - well-known user applications that do not need to start with Windows.
* HIGH_IMPACT      - optional items known to be heavy, or measured using a lot of RAM right now.
* UNKNOWN          - everything else: shown for manual review, never auto-recommended.

Startup "impact" is an *estimate*: Windows only exposes Task Manager's measured impact
through private telemetry, so BoostAI combines the catalog below with the item's current
measured memory usage (if it is running) and says which of the two it used.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from boostai.core.models import MB, ProcessInfo, StartupItem, fmt_bytes


class StartupCategory(str, Enum):
    RECOMMENDED_KEEP = "RECOMMENDED_KEEP"
    OPTIONAL = "OPTIONAL"
    HIGH_IMPACT = "HIGH_IMPACT"
    UNKNOWN = "UNKNOWN"
    PROTECTED = "PROTECTED"


@dataclass(slots=True)
class StartupAssessment:
    category: StartupCategory
    reason: str
    impact: str               # "High" | "Medium" | "Low" | "Unknown"
    impact_basis: str         # how the impact was estimated
    running_memory: int | None = None


PROTECTED_KEYWORDS = (
    "securityhealth", "windowsdefender", "defender", "antivirus", "malwarebytes", "kaspersky", "avast", "avg",
    "bitdefender", "eset security", "egui.exe", "norton", "mcafee", "sophos", "trend micro", "crowdstrike", "sentinel",
)

KEEP_KEYWORDS = (
    "realtek", "rtkaud", "synaptics", "syntp", "elan", "etdctrl", "dolby", "waves", "maxxaudio", "conexant",
    "cirrus", "igfx", "intel(r) graphics", "hotkey", "touchpad", "precision touchpad", "armoury", "asus smart display",
    "lenovo vantage", "dell", "hp hotkey", "fnhotkey", "bluetooth", "nahimic", "thunderbolt",
)

# (keyword, heavy?) - matched against value name, target file name and publisher.
OPTIONAL_APPS: tuple[tuple[str, bool], ...] = (
    ("discord", True), ("spotify", True), ("steam", True), ("epicgameslauncher", True), ("ea desktop", True),
    ("ealauncher", True), ("eadm", True), ("origin", True), ("ubisoft", True), ("upc.exe", True),
    ("battle.net", True), ("teams", True), ("ms-teams", True), ("skype", False), ("zoom", True), ("slack", True),
    ("microsoftedgeautolaunch", True), ("msedge", True), ("opera", True), ("chrome", True), ("brave", True),
    ("creative cloud", True), ("adobe", False), ("ccleaner", False), ("utorrent", True), ("qbittorrent", False),
    ("itunes", False), ("medal", True), ("overwolf", True), ("roblox", True), ("ollama", False),
    ("whatsapp", False), ("telegram", False), ("dropbox", False), ("onedrive", False), ("googledrivefs", False),
    ("google drive", False), ("razer", False), ("icue", True), ("logi", False), ("lghub", True), ("radmin", False),
    ("nordvpn", False), ("expressvpn", False), ("wallpaper", True), ("rainmeter", False), ("docker", True),
    ("virtual pet", False), ("amd_dc_opt", False), ("cortana", False), ("xbox", False), ("gog galaxy", True),
)

CLOUD_SYNC = ("onedrive", "dropbox", "googledrivefs", "google drive")


def _haystack(item: StartupItem) -> str:
    parts = [item.name, item.executable_name or "", item.publisher or "", item.target_path or ""]
    return " ".join(parts).lower()


def _running_memory(item: StartupItem, processes: list[ProcessInfo]) -> int | None:
    exe = item.executable_name
    if not exe:
        return None
    total = sum(p.private for p in processes if p.name.lower() == exe)
    return total or None


def assess(item: StartupItem, processes: list[ProcessInfo] | None = None) -> StartupAssessment:
    hay = _haystack(item)
    processes = processes or []
    running = _running_memory(item, processes)
    sysroot = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))

    if any(k in hay for k in PROTECTED_KEYWORDS) or (
        item.target_path and os.path.normcase(item.target_path).startswith(sysroot)
    ):
        return StartupAssessment(StartupCategory.PROTECTED, "Security software or Windows component.", "n/a", "not assessed")

    if any(k in hay for k in KEEP_KEYWORDS):
        return StartupAssessment(
            StartupCategory.RECOMMENDED_KEEP,
            "Hardware/driver utility (audio, input, display or hotkeys). Disabling can break device features.",
            "Low", "catalog", running,
        )

    for keyword, heavy in OPTIONAL_APPS:
        if keyword in hay:
            note = "Well-known application that does not need to start with Windows."
            if any(c in hay for c in CLOUD_SYNC):
                note = "Cloud file sync. Disable only if you do not need files to sync automatically after sign-in."
            measured_heavy = running is not None and running >= 300 * MB
            if heavy or measured_heavy:
                basis = f"measured: currently {fmt_bytes(running)} RAM" if running else "catalog (known heavy app)"
                return StartupAssessment(StartupCategory.HIGH_IMPACT, note, "High", basis, running)
            basis = f"measured: currently {fmt_bytes(running)} RAM" if running else "catalog"
            impact = "Medium" if running and running >= 100 * MB else "Low"
            return StartupAssessment(StartupCategory.OPTIONAL, note, impact, basis, running)

    impact, basis = "Unknown", "not in catalog"
    if running:
        impact = "High" if running >= 300 * MB else "Medium" if running >= 100 * MB else "Low"
        basis = f"measured: currently {fmt_bytes(running)} RAM"
    return StartupAssessment(
        StartupCategory.UNKNOWN, "Not recognised. Review manually before disabling.", impact, basis, running
    )
