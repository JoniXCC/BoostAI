"""Composition root: wires collectors, storage, detectors, actions and AI together.

The UI talks only to :class:`BoostEngine`; nothing in here depends on Qt, so the whole
pipeline is usable from tests and scripts.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from boostai.actions.action_models import ActionOutcome, ActionRequest, Approval, ResultStatus
from boostai.actions.elevation import ElevationBroker
from boostai.actions.executor import ActionExecutor
from boostai.actions.handlers.base import ActionContext
from boostai.actions.registry import ActionRegistry
from boostai.actions.rollback import RollbackManager
from boostai.actions.system_ops import SystemOps, WindowsSystemOps
from boostai.ai.advisor import AIAdvisor
from boostai.config import paths
from boostai.config.logging_config import log_event
from boostai.config.settings import Settings, SettingsStore
from boostai.core.baselines import BaselineEngine
from boostai.core.comparison import Comparison, MeasuredState, compare, measure_state
from boostai.core.diagnostics import DiagnosticsEngine
from boostai.core.history import History
from boostai.core.models import SystemInfo
from boostai.core.modes import GamingCheck, gaming_check
from boostai.core.monitor import Monitor
from boostai.core.sampler import SystemSampler
from boostai.core.scanner import Progress, ScanResult, Scanner
from boostai.database.database import Database
from boostai.database.repository import Repository
from boostai.metrics import power, services
from boostai.metrics.system_info import collect_system_info
from boostai.security.protected_processes import current_username
from boostai.utils import winapi

log = logging.getLogger(__name__)

CHANGING = {ResultStatus.SUCCESS, ResultStatus.NO_MEANINGFUL_CHANGE, ResultStatus.PARTIAL}


@dataclass
class PlannedAction:
    request: ActionRequest
    approval: Approval


@dataclass
class OptimizationReport:
    session_id: str
    outcomes: list[ActionOutcome]
    before: MeasuredState | None = None
    after: MeasuredState | None = None
    comparison: Comparison | None = None
    comparison_id: int | None = None
    notes: list[str] = field(default_factory=list)


class BoostEngine:
    def __init__(self, settings_store: SettingsStore | None = None, db_path: Path | str | None = None,
                 ops: SystemOps | None = None, start_monitor: bool = True) -> None:
        self.settings_store = settings_store or SettingsStore()
        self.db = Database(db_path or paths.database_path())
        self.repo = Repository(self.db)
        self.registry = ActionRegistry()
        m = self.settings.monitor
        self.history = History(m.history_minutes, m.process_interval_s, m.system_interval_s)
        self.baselines = BaselineEngine(self.repo)
        self.sampler = SystemSampler()
        self.ops: SystemOps = ops or WindowsSystemOps(self.sampler.processes)
        self.broker = ElevationBroker()
        self.executor = ActionExecutor(self.registry, self.action_context, self.repo, self.broker,
                                       mode_provider=lambda: self.settings.mode)
        self.rollback_manager = RollbackManager(self.registry, self.action_context, self.repo, self.broker)
        self.diagnostics = DiagnosticsEngine(self.registry)
        self._system_info: SystemInfo | None = None
        self.scanner = Scanner(self.sampler, self.history, self.baselines, self.diagnostics, self.repo,
                               lambda: self.settings, self.system_info)
        self.advisor = AIAdvisor(lambda: self.settings.ai, self.registry)
        self.monitor = Monitor(self.sampler, self.history, self.repo, lambda: self.settings, self.baselines)
        self.last_scan: ScanResult | None = None
        self._warm_start()
        if self.baselines.count() == 0:
            threading.Thread(target=self._safe_baseline_refresh, daemon=True).start()
        if start_monitor:
            self.monitor.start()

    # --------------------------------------------------------------- basics
    @property
    def settings(self) -> Settings:
        return self.settings_store.settings

    def save_settings(self, settings: Settings) -> None:
        self.settings_store.save(settings)

    def system_info(self) -> SystemInfo:
        if self._system_info is None:
            self._system_info = collect_system_info()
        return self._system_info

    def action_context(self) -> ActionContext:
        return ActionContext(ops=self.ops, settings=self.settings, self_pid=os.getpid(), user=current_username(),
                             is_admin=winapi.is_admin())

    def _warm_start(self) -> None:
        try:
            since = time.time() - self.settings.monitor.history_minutes * 60
            self.history.warm_start(self.repo.process_samples_since(since))
        except Exception:
            log.exception("History warm start failed")

    def _safe_baseline_refresh(self) -> None:
        try:
            self.baselines.refresh()
        except Exception:
            log.exception("Baseline refresh failed")

    # ---------------------------------------------------------------- scans
    def run_scan(self, kind: str = "quick", progress: Progress | None = None,
                 cancel: threading.Event | None = None) -> ScanResult:
        result = self.scanner.run(kind, progress, cancel)
        self.last_scan = result
        return result

    def deep_scan(self, minutes: float, progress: Progress | None = None,
                  cancel: threading.Event | None = None) -> ScanResult:
        """Observe the system at a higher sampling rate for ``minutes``, then analyse. Changes nothing."""
        progress = progress or (lambda _p, _m: None)
        total = max(1.0, minutes * 60)
        log_event("deep_scan_started", minutes=minutes)
        if not self.monitor.running:
            self.monitor.start()
        self.monitor.boost_sampling(total + 30)
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed >= total:
                break
            if cancel is not None and cancel.wait(1.0):
                break
            if cancel is None:
                time.sleep(1.0)
            remaining = int(total - elapsed)
            progress(int(80 * elapsed / total),
                     f"Observing memory and CPU trends... {remaining // 60}:{remaining % 60:02d} remaining")
        self.monitor.boost_sampling(0)
        result = self.run_scan("deep", lambda p, msg: progress(80 + p // 5, msg), None)
        log_event("deep_scan_finished", minutes=round((time.time() - start) / 60, 1), issues=len(result.issues))
        return result

    # --------------------------------------------------------- optimisation
    def validate(self, request: ActionRequest):
        return self.executor.validate(request)

    def measure(self, seconds: float = 10.0) -> MeasuredState:
        return measure_state(self.sampler, seconds)

    def apply(self, planned: list[PlannedAction], progress: Callable[[int, str], None] | None = None,
              session_id: str | None = None, measure: bool = True, measure_seconds: float = 10.0,
              sleep: Callable[[float], None] = time.sleep) -> OptimizationReport:
        """Before-measure -> execute approved actions (each verified) -> settle -> after-measure -> compare."""
        progress = progress or (lambda _p, _m: None)
        report = OptimizationReport(session_id=session_id or uuid.uuid4().hex, outcomes=[])
        if measure:
            progress(2, f"Measuring the system before changes ({int(measure_seconds)} s)...")
            report.before = self.measure(measure_seconds)
        n = max(1, len(planned))
        for idx, item in enumerate(planned):
            progress(10 + int(60 * idx / n), f"Applying: {item.request.action_id.replace('_', ' ').title()}")
            outcome = self.executor.execute(item.request, item.approval, report.session_id)
            report.outcomes.append(outcome)
        changed = [o for o in report.outcomes if o.status in CHANGING]
        if measure and changed:
            settle = self.settings.settle_seconds
            progress(75, f"Letting the system settle ({int(settle)} s)...")
            sleep(settle)
            progress(85, f"Measuring the system after changes ({int(measure_seconds)} s)...")
            report.after = self.measure(measure_seconds)
            report.comparison = compare(report.before, report.after)
            report.comparison_id = self.repo.insert_comparison(
                report.before.to_dict(), report.after.to_dict(), report.comparison.to_dict(),
                [o.record_id for o in report.outcomes if o.record_id],
            )
        elif measure:
            report.notes.append("No change was applied, so no after-measurement was taken.")
        progress(100, "Done")
        return report

    def undo(self, record_id: int):
        return self.rollback_manager.rollback(record_id)

    # ------------------------------------------------------- background watch
    def watch(self) -> list:
        """Cheap periodic check (no scan): likely leaks and severe memory pressure from monitor history."""
        from boostai.core.issues import Confidence, Severity
        from boostai.detection import memory_leak_detector, memory_pressure_detector
        from boostai.detection.base import DetectionContext

        snap = self.monitor.latest
        if snap is None or not snap.processes:
            return []
        ctx = DetectionContext(snapshot=snap, system_info=self.system_info(), history=self.history,
                               baselines=self.baselines, settings=self.settings.detection)
        leaks = memory_leak_detector.detect(ctx)
        names = {str(i.metrics.get("process", "")).lower() for i in leaks}
        found = leaks + memory_pressure_detector.detect(ctx, names)
        return [i for i in found if i.severity in (Severity.HIGH, Severity.CRITICAL) and i.confidence != Confidence.LOW]

    # ---------------------------------------------------------- gaming mode
    def gaming_check(self) -> GamingCheck:
        snap = self.sampler.snapshot(include_processes=True, include_temperatures=True, include_power=True)
        svc_pids = {s.pid for s in services.collect_services() if s.pid}
        return gaming_check(snap, power.list_power_plans(), os.getpid(), svc_pids, current_username())

    def start_gaming_mode(self, planned: list[PlannedAction], progress=None) -> OptimizationReport:
        session = f"gaming-{uuid.uuid4().hex[:12]}"
        log_event("gaming_mode_started", session=session, actions=len(planned))
        report = self.apply(planned, progress, session_id=session, measure=True, measure_seconds=5)
        settings = self.settings.model_copy(update={"active_gaming_session": session})
        self.save_settings(settings)
        return report

    def end_gaming_mode(self, restore: bool = True):
        session = self.settings.active_gaming_session
        results = []
        if session and restore:
            results = self.rollback_manager.rollback_session(session)
        self.save_settings(self.settings.model_copy(update={"active_gaming_session": None}))
        log_event("gaming_mode_ended", session=session, restored=len(results))
        return results

    # ------------------------------------------------------------- lifecycle
    def shutdown(self) -> None:
        try:
            self.monitor.stop()
        finally:
            self.sampler.close()
            self.db.close()
