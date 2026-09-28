"""Deterministic process-protection rules.

BoostAI refuses to close, restart or modify a process unless *every* rule passes.
When anything is uncertain (owner unknown, path unknown, service host, ...) the
answer is "blocked". AI output never participates in this decision.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from boostai.core.models import ProcessInfo

# Core Windows components. Terminating most of these crashes the session or the OS.
CRITICAL_PROCESSES = frozenset(
    n.lower()
    for n in (
        "System", "Registry", "Secure System", "MemCompression", "Memory Compression", "smss.exe",
        "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe",
        "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe", "taskhostw.exe",
        "spoolsv.exe", "audiodg.exe", "conhost.exe", "dllhost.exe", "WmiPrvSE.exe", "RuntimeBroker.exe",
        "SearchIndexer.exe", "SearchHost.exe", "StartMenuExperienceHost.exe", "ShellExperienceHost.exe",
        "ShellHost.exe", "TextInputHost.exe", "LogonUI.exe", "userinit.exe", "SgrmBroker.exe", "wlanext.exe",
        "dasHost.exe", "WUDFHost.exe", "vmcompute.exe", "vmmem", "vmmemWSL", "System Idle Process",
        "LockApp.exe", "ApplicationFrameHost.exe", "SystemSettings.exe", "smartscreen.exe", "consent.exe",
        "SecurityHealthService.exe", "SecurityHealthSystray.exe", "MsMpEng.exe", "NisSrv.exe",
        "MpDefenderCoreService.exe", "MpCmdRun.exe", "SenseNdr.exe", "MsSense.exe", "wuauclt.exe",
        "TrustedInstaller.exe", "TiWorker.exe", "UsoClient.exe", "MoUsoCoreWorker.exe", "WaaSMedicAgent.exe",
        "msiexec.exe", "CompPkgSrv.exe", "AggregatorHost.exe", "LsaIso.exe", "unsecapp.exe", "wermgr.exe",
        "WerFault.exe", "NVDisplay.Container.exe", "atiesrxx.exe", "atieclxx.exe", "igfxEM.exe",
    )
)

# Third-party security software: never touched, even if owned by the user.
SECURITY_SOFTWARE = frozenset(
    n.lower()
    for n in (
        "avp.exe", "avpui.exe", "ekrn.exe", "egui.exe", "bdagent.exe", "bdservicehost.exe", "vsserv.exe",
        "mbam.exe", "mbamservice.exe", "mbamtray.exe", "avgui.exe", "avgsvc.exe", "avastui.exe", "avastsvc.exe",
        "mcshield.exe", "mfemms.exe", "mcuicnt.exe", "nortonsecurity.exe", "ns.exe", "ccsvchst.exe",
        "sophosui.exe", "savservice.exe", "csfalconservice.exe", "sentinelagent.exe", "cylancesvc.exe",
        "wrsa.exe", "psuaservice.exe", "360tray.exe", "zatray.exe", "hitmanpro.exe",
    )
)

# Name fragments that indicate security/anti-cheat/driver components.
PROTECTED_NAME_FRAGMENTS = ("defender", "antivirus", "anticheat", "easyanticheat", "battleye", "vanguard", "vgc")


@dataclass(slots=True)
class ProtectionVerdict:
    protected: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        if not self.protected:
            return "Allowed"
        return "Action blocked for safety: " + "; ".join(self.reasons)


def current_username() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _system_root() -> str:
    return os.path.normcase(os.path.abspath(os.environ.get("SystemRoot", r"C:\Windows")))


def evaluate_process(
    proc: ProcessInfo,
    *,
    self_pid: int,
    service_pids: set[int] | frozenset[int] = frozenset(),
    user: str | None = None,
) -> ProtectionVerdict:
    """Return whether BoostAI may close/restart/trim ``proc``."""
    reasons: list[str] = []
    lname = proc.name.lower()
    user = user or current_username()

    if proc.pid <= 4:
        reasons.append("kernel/system process")
    if proc.pid == self_pid:
        reasons.append("this is BoostAI itself")
    if lname in CRITICAL_PROCESSES:
        reasons.append(f"{proc.name} is a critical Windows process")
    if lname in SECURITY_SOFTWARE or any(f in lname for f in PROTECTED_NAME_FRAGMENTS):
        reasons.append(f"{proc.name} appears to be security or anti-cheat software")
    if proc.session_id == 0:
        reasons.append("runs in the services session (session 0)")
    if proc.pid in service_pids:
        reasons.append("hosts a Windows service")
    if not proc.username:
        reasons.append("owner could not be verified")
    elif proc.username.lower() != user.lower():
        reasons.append(f"owned by another account ({proc.username})")
    if not proc.exe:
        reasons.append("executable path could not be verified")
    else:
        exe = os.path.normcase(os.path.abspath(proc.exe))
        if exe.startswith(_system_root() + os.sep):
            reasons.append("Windows system component")
    return ProtectionVerdict(protected=bool(reasons), reasons=reasons)
