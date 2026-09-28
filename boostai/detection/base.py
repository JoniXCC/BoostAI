"""Shared context and helpers for the rule-based detectors."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from boostai.actions.action_models import ActionID
from boostai.actions.cleanup_catalog import TargetEstimate
from boostai.config.settings import DetectionSettings
from boostai.core.baselines import BaselineEngine
from boostai.core.history import History
from boostai.core.issues import ActionProposal
from boostai.core.models import PowerPlan, ProcessInfo, ServiceInfo, StartupItem, SystemInfo, SystemSnapshot
from boostai.security.protected_processes import ProtectionVerdict, current_username, evaluate_process

GAME_PATH_MARKERS = (
    "\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\gog galaxy\\games\\", "\\ea games\\",
    "\\ubisoft game launcher\\games\\", "\\xboxgames\\", "\\battle.net\\", "\\roblox\\versions\\",
    "\\minecraft", "\\games\\",
)


@dataclass
class DetectionContext:
    snapshot: SystemSnapshot
    system_info: SystemInfo
    history: History
    baselines: BaselineEngine
    settings: DetectionSettings
    startup_items: list[StartupItem] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)
    cleanup: list[TargetEstimate] | None = None
    power_plans: list[PowerPlan] = field(default_factory=list)
    self_pid: int = field(default_factory=os.getpid)
    user: str = field(default_factory=current_username)
    fullscreen_foreground: bool = False
    gaming_mode: bool = False
    on_battery: bool = False
    startup_keep_ids: frozenset[str] = frozenset()
    now: float = field(default_factory=time.time)
    _verdicts: dict = field(default_factory=dict, repr=False)

    @property
    def service_pids(self) -> set[int]:
        return {s.pid for s in self.services if s.pid}

    @property
    def total_ram(self) -> int:
        return self.snapshot.memory.total

    def protection(self, proc: ProcessInfo) -> ProtectionVerdict:
        key = proc.key
        if key not in self._verdicts:
            self._verdicts[key] = evaluate_process(proc, self_pid=self.self_pid, service_pids=self.service_pids,
                                                   user=self.user)
        return self._verdicts[key]

    def is_game(self, proc: ProcessInfo) -> bool:
        exe = (proc.exe or "").lower()
        if any(m in exe for m in GAME_PATH_MARKERS):
            return True
        return proc.is_foreground and self.fullscreen_foreground

    def processes_named(self, lname: str) -> list[ProcessInfo]:
        return [p for p in self.snapshot.processes if p.name.lower() == lname]


def process_params(proc: ProcessInfo) -> dict:
    return {"pid": proc.pid, "create_time": proc.create_time, "name": proc.name}


def root_instance(instances: list[ProcessInfo]) -> ProcessInfo:
    pids = {p.pid for p in instances}
    roots = [p for p in instances if p.ppid not in pids]
    return max(roots or instances, key=lambda p: (p.has_window, p.private))


def restart_or_close_proposals(proc: ProcessInfo, ctx: DetectionContext, *, restart: bool = True,
                               close: bool = True, reason: str = "") -> list[ActionProposal]:
    """Offer process actions only when the deterministic protection rules allow them."""
    if ctx.protection(proc).protected:
        return []
    proposals: list[ActionProposal] = []
    if restart and proc.exe:
        proposals.append(ActionProposal(
            action_id=ActionID.RESTART_PROCESS.value, params=process_params(proc), label=f"Restart {proc.name}",
            rationale=reason or "Restarting releases memory the application has accumulated.",
            warning=f"Restarting {proc.name} may close active windows. Save your work first.",
        ))
    if close:
        proposals.append(ActionProposal(
            action_id=ActionID.CLOSE_USER_PROCESS.value, params=process_params(proc), label=f"Close {proc.name}",
            rationale=reason or "Closing frees all memory and CPU used by the application.",
            warning=f"Closing {proc.name} may lose unsaved work.",
        ))
    return proposals
