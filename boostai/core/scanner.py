"""Full system scan: collect everything, diagnose, score, persist."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import psutil

from boostai.actions.cleanup_catalog import TEMP_TARGETS, TargetEstimate, estimate
from boostai.config.logging_config import log_event
from boostai.config.settings import Settings
from boostai.core.baselines import BaselineEngine
from boostai.core.diagnostics import DiagnosticsEngine
from boostai.core.history import History
from boostai.core.issues import Issue
from boostai.core.models import PowerPlan, ServiceInfo, StartupItem, SystemInfo, SystemSnapshot
from boostai.core.performance_score import HealthScore
from boostai.core.sampler import SystemSampler
from boostai.database.repository import Repository
from boostai.detection.base import DetectionContext
from boostai.metrics import power, services, startup
from boostai.utils import winapi

log = logging.getLogger(__name__)

Progress = Callable[[int, str], None]


class ScanCancelled(Exception):
    pass


@dataclass
class ScanResult:
    kind: str
    started: float
    duration: float
    snapshot: SystemSnapshot
    system_info: SystemInfo
    startup_items: list[StartupItem]
    services: list[ServiceInfo]
    cleanup: list[TargetEstimate]
    power_plans: list[PowerPlan]
    issues: list[Issue]
    score: HealthScore
    scan_id: int | None = None
    notes: list[str] = field(default_factory=list)


class Scanner:
    def __init__(self, sampler: SystemSampler, history: History, baselines: BaselineEngine,
                 diagnostics: DiagnosticsEngine, repo: Repository | None, settings_provider: Callable[[], Settings],
                 system_info_provider: Callable[[], SystemInfo]) -> None:
        self.sampler = sampler
        self.history = history
        self.baselines = baselines
        self.diagnostics = diagnostics
        self.repo = repo
        self.settings_provider = settings_provider
        self.system_info_provider = system_info_provider
        self._lock = threading.Lock()

    def run(self, kind: str = "quick", progress: Progress | None = None,
            cancel: threading.Event | None = None) -> ScanResult:
        progress = progress or (lambda _p, _m: None)

        def step(pct: int, msg: str) -> None:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled()
            progress(pct, msg)

        with self._lock:
            started = time.time()
            settings = self.settings_provider()
            log_event("scan_started", kind=kind)
            try:
                step(5, "Collecting system information")
                info = self.system_info_provider()
                step(15, "Measuring CPU, memory, disk and temperatures")
                snap = self.sampler.snapshot(include_processes=True, include_temperatures=True, include_power=True)
                if len(self.history.system_points()) < 3:
                    # No monitor history yet: take a second sample so CPU% covers a real interval.
                    time.sleep(1.5)
                    snap = self.sampler.snapshot(include_processes=True, include_temperatures=True, include_power=True)
                self.history.add_processes(snap.timestamp, snap.processes)
                self.history.add_system_snapshot(snap)
                step(35, f"Inspected {snap.process_count} processes")
                step(45, "Reading startup programs")
                startup_items = startup.collect_startup_items()
                step(55, "Inspecting Windows services")
                svc = services.collect_services()
                step(65, "Estimating safe temporary files (nothing is deleted)")
                running = {p.name for p in snap.processes}
                # Only the fast temp targets during scans; large caches are measured on demand (Cleanup page).
                cleanup = estimate(list(TEMP_TARGETS), settings.cleanup_min_age_hours, running, winapi.is_admin())
                plans = power.list_power_plans()
                step(80, "Analysing")
                ctx = DetectionContext(
                    snapshot=snap, system_info=info, history=self.history, baselines=self.baselines,
                    settings=settings.detection, startup_items=startup_items, services=svc, cleanup=cleanup,
                    power_plans=plans, fullscreen_foreground=winapi.foreground_is_fullscreen(),
                    on_battery=_on_battery(), startup_keep_ids=frozenset(settings.startup_keep_ids),
                )
                report = self.diagnostics.run(ctx)
                result = ScanResult(kind, started, time.time() - started, snap, info, startup_items, svc, cleanup,
                                    plans, report.issues, report.score)
                step(92, "Saving results")
                self._persist(result)
                for issue in report.issues:
                    log_event("issue_detected", key=issue.key, severity=issue.severity.value,
                              confidence=issue.confidence.value)
                    for p in issue.proposals:
                        log_event("action_recommended", issue=issue.key, action_id=p.action_id)
                log_event("scan_finished", kind=kind, issues=len(report.issues), score=report.score.overall,
                          seconds=round(result.duration, 2))
                step(100, "Scan complete")
                return result
            except ScanCancelled:
                log_event("scan_failed", kind=kind, reason="cancelled")
                raise
            except Exception as exc:
                log.exception("Scan failed")
                log_event("scan_failed", kind=kind, reason=str(exc))
                raise

    def _persist(self, r: ScanResult) -> None:
        if self.repo is None:
            return
        sysdisk = r.snapshot.system_disk
        r.scan_id = self.repo.insert_scan({
            "timestamp": r.started, "kind": r.kind, "cpu_usage": r.snapshot.cpu.total_percent,
            "memory_usage": r.snapshot.memory.percent, "memory_used": r.snapshot.memory.used,
            "memory_total": r.snapshot.memory.total, "available_memory": r.snapshot.memory.available,
            "commit_percent": r.snapshot.memory.commit_percent, "memory_pressure": r.snapshot.memory_pressure.value,
            "disk_usage": sysdisk.percent if sysdisk else None, "process_count": r.snapshot.process_count,
            "health_score": r.score.overall, "scores_json": json.dumps(r.score.to_dict()),
            "issue_count": len(r.issues),
        })
        self.repo.insert_issues(r.scan_id, r.issues)


def _on_battery() -> bool:
    try:
        battery = psutil.sensors_battery()
        return bool(battery is not None and not battery.power_plugged)
    except (RuntimeError, OSError, NotImplementedError):
        return False
