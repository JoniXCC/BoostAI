"""Service safety classification.

Services are classified with an explicit *allow-list*: only services listed in
``OPTIONAL_SERVICES`` can ever be stopped or restarted by BoostAI. Anything else is
either explicitly PROTECTED (with a reason) or UNKNOWN ("Manual review required").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ServiceClass(str, Enum):
    PROTECTED = "PROTECTED"
    OPTIONAL = "OPTIONAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class OptionalService:
    name: str
    description: str
    restartable: bool = True
    stoppable: bool = True


# Conservative allow-list. Stopping these is temporary (start type is never changed),
# and each only affects an optional feature until the service is started again.
OPTIONAL_SERVICES: dict[str, OptionalService] = {
    s.name.lower(): s
    for s in (
        OptionalService("WSearch", "Windows Search indexer. Stopping pauses indexing; search still works but may be slower."),
        OptionalService("SysMain", "Superfetch/SysMain prefetching. Can cause disk activity on HDD systems."),
        OptionalService("DiagTrack", "Connected User Experiences and Telemetry."),
        OptionalService("dmwappushservice", "Device management WAP push routing (telemetry related)."),
        OptionalService("Spooler", "Print Spooler. Printing will not work while stopped."),
        OptionalService("Fax", "Fax service."),
        OptionalService("MapsBroker", "Downloaded Maps Manager."),
        OptionalService("WMPNetworkSvc", "Windows Media Player network sharing."),
        OptionalService("RetailDemo", "Retail demo experience."),
        OptionalService("XblGameSave", "Xbox Live game save sync."),
        OptionalService("XboxNetApiSvc", "Xbox Live networking."),
        OptionalService("XblAuthManager", "Xbox Live authentication manager."),
        OptionalService("edgeupdate", "Microsoft Edge updater (third-party updater, restarts on schedule)."),
        OptionalService("edgeupdatem", "Microsoft Edge updater (on-demand)."),
        OptionalService("gupdate", "Google updater."),
        OptionalService("gupdatem", "Google updater (on-demand)."),
        OptionalService("GoogleUpdaterService", "Google updater."),
        OptionalService("GoogleUpdaterInternalService", "Google updater (internal)."),
        OptionalService("AdobeARMservice", "Adobe Acrobat update service."),
        OptionalService("MozillaMaintenance", "Mozilla maintenance/update service."),
        OptionalService("brave", "Brave browser updater."),
        OptionalService("bravem", "Brave browser updater (on-demand)."),
        OptionalService("ClickToRunSvc", "Microsoft Office Click-to-Run (Office apps need it while running).", stoppable=False),
        OptionalService("OneSyncSvc", "Sync host for mail/contacts/calendar.", stoppable=False),
        OptionalService("PcaSvc", "Program Compatibility Assistant."),
        OptionalService("TabletInputService", "Touch keyboard and handwriting panel.", stoppable=False),
    )
}

# Explicitly protected: security, networking, RPC, session, storage, auth, update, audio, power.
PROTECTED_SERVICES: dict[str, str] = {
    **{n.lower(): "Security" for n in (
        "WinDefend", "WdNisSvc", "WdFilter", "WdBoot", "Sense", "SecurityHealthService", "wscsvc", "mpssvc",
        "BFE", "SgrmBroker", "MDCoreSvc", "webthreatdefsvc", "webthreatdefusersvc", "SamSs", "VaultSvc",
        "KeyIso", "EFS", "CryptSvc", "AppIDSvc", "ProtectedStorage", "mpsdrv",
    )},
    **{n.lower(): "RPC / core infrastructure" for n in (
        "RpcSs", "RpcEptMapper", "DcomLaunch", "LSM", "Power", "PlugPlay", "ProfSvc", "Schedule", "EventLog",
        "SENS", "SystemEventsBroker", "BrokerInfrastructure", "CoreMessagingRegistrar", "StateRepository",
        "TimeBrokerSvc", "UserManager", "Winmgmt", "gpsvc", "TrustedInstaller", "msiserver", "camsvc",
        "AppXSvc", "ClipSVC", "TokenBroker", "Themes", "FontCache", "ShellHWDetection", "DispBrokerDesktopSvc",
    )},
    **{n.lower(): "Networking" for n in (
        "Dhcp", "Dnscache", "NlaSvc", "netprofm", "nsi", "LanmanWorkstation", "LanmanServer", "WlanSvc",
        "Wcmsvc", "WinHttpAutoProxySvc", "iphlpsvc", "Netman", "NcbService", "BthAvctpSvc", "bthserv",
        "RasMan", "IKEEXT", "PolicyAgent", "Tcpip", "WwanSvc", "Netlogon",
    )},
    **{n.lower(): "Windows Update infrastructure" for n in (
        "wuauserv", "UsoSvc", "WaaSMedicSvc", "BITS", "DoSvc", "InstallService",
    )},
    **{n.lower(): "Audio / devices / storage" for n in (
        "Audiosrv", "AudioEndpointBuilder", "StorSvc", "VSS", "swprv", "vds", "DeviceInstall",
        "DeviceAssociationService", "DsmSvc", "hidserv", "TrkWks", "WPDBusEnum",
    )},
    **{n.lower(): "Authentication / session" for n in (
        "Winlogon", "seclogon", "UmRdpService", "TermService", "SessionEnv", "NgcSvc", "NgcCtnrSvc",
        "WbioSrvc", "CDPUserSvc", "LicenseManager",
    )},
}


def classify_service(name: str) -> tuple[ServiceClass, str]:
    key = name.lower()
    # Per-user service instances look like "CDPUserSvc_1a2b3c"; classify by their base name.
    base = key.split("_", 1)[0] if "_" in key else key
    for candidate in (key, base):
        if candidate in PROTECTED_SERVICES:
            return ServiceClass.PROTECTED, f"Protected ({PROTECTED_SERVICES[candidate]})"
        if candidate in OPTIONAL_SERVICES:
            return ServiceClass.OPTIONAL, OPTIONAL_SERVICES[candidate].description
    if any(f in key for f in ("defend", "antivirus", "firewall", "security", "update", "crypt", "auth")):
        return ServiceClass.PROTECTED, "Protected (security/update related name)"
    return ServiceClass.UNKNOWN, "Manual review required."


def optional_service(name: str) -> OptionalService | None:
    return OPTIONAL_SERVICES.get(name.lower())
