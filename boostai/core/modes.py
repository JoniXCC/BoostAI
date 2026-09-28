"""Gaming Mode pre-check. (Safe/Balanced are enforced by the validator; Deep Scan lives in the engine.)

Gaming Mode never terminates anything by itself. It measures free resources, lists
optional heavy applications the user may choose to close, and suggests a higher-
performance power plan if one exists. Reversible changes are tagged with a session ID so
they can be restored when Gaming Mode ends.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from boostai.core.models import MB, PowerPlan, ProcessGroup, ProcessInfo, SystemSnapshot, group_processes
from boostai.detection.base import GAME_PATH_MARKERS, root_instance
from boostai.metrics.power import HIGH_PERFORMANCE, ULTIMATE_PERFORMANCE
from boostai.security.protected_processes import evaluate_process

HEAVY_APP_MIN = 150 * MB
PERFORMANCE_NAME_HINTS = ("performance", "turbo", "gaming", "game")


@dataclass(slots=True)
class HeavyApp:
    group: ProcessGroup
    root: ProcessInfo
    protected: bool
    reason: str


@dataclass(slots=True)
class GamingCheck:
    available_ram: int
    total_ram: int
    ram_percent: float
    cpu_percent: float
    heavy_apps: list[HeavyApp]
    active_plan: PowerPlan | None
    recommended_plan: PowerPlan | None
    temperatures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def recommend_plan(plans: list[PowerPlan]) -> PowerPlan | None:
    active = next((p for p in plans if p.active), None)
    if active and (active.guid in (HIGH_PERFORMANCE, ULTIMATE_PERFORMANCE)
                   or any(h in active.name.lower() for h in PERFORMANCE_NAME_HINTS)):
        return None  # already on a performance plan
    for guid in (HIGH_PERFORMANCE, ULTIMATE_PERFORMANCE):
        plan = next((p for p in plans if p.guid == guid), None)
        if plan:
            return plan
    return next((p for p in plans if any(h in p.name.lower() for h in PERFORMANCE_NAME_HINTS)), None)


def gaming_check(snap: SystemSnapshot, plans: list[PowerPlan], self_pid: int, service_pids: set[int], user: str) -> GamingCheck:
    heavy: list[HeavyApp] = []
    for g in group_processes(snap.processes):
        if g.rss < HEAVY_APP_MIN:
            continue
        instances = [p for p in snap.processes if p.name.lower() == g.name.lower()]
        root = root_instance(instances)
        exe = (root.exe or "").lower()
        if g.is_foreground or any(m in exe for m in GAME_PATH_MARKERS):
            continue  # the game itself / the app in use
        verdict = evaluate_process(root, self_pid=self_pid, service_pids=service_pids, user=user)
        reason = "; ".join(verdict.reasons) if verdict.protected else (
            "has open windows" if g.has_window else "runs in the background")
        heavy.append(HeavyApp(g, root, verdict.protected, reason))
    heavy.sort(key=lambda h: (h.protected, -h.group.rss))
    check = GamingCheck(
        available_ram=snap.memory.available, total_ram=snap.memory.total, ram_percent=snap.memory.percent,
        cpu_percent=snap.cpu.total_percent, heavy_apps=heavy,
        active_plan=next((p for p in plans if p.active), None), recommended_plan=recommend_plan(plans),
        temperatures=[f"{r.sensor}: {r.celsius:.0f} °C" for r in snap.temperatures.readings],
    )
    if snap.memory.available < 4 * 1024**3:
        check.notes.append("Less than 4 GB of RAM is free. Modern games often need 6-10 GB; closing apps helps.")
    if snap.cpu.total_percent > 30:
        check.notes.append(f"CPU is already {snap.cpu.total_percent:.0f}% busy before the game starts.")
    if check.recommended_plan is None and check.active_plan:
        check.notes.append(f"Power plan '{check.active_plan.name}' is already suited to gaming (or no faster plan exists).")
    return check
