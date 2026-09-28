"""Lightweight background monitor.

Performance budget (the optimizer must not become the problem):
* one native process snapshot every ``process_interval_s`` (~5-40 ms on a 350-process PC);
* system counters every ``system_interval_s``; both slow down when the window is hidden;
* database writes are batched: system samples are averaged per persist interval
  (~1 row/minute) and only the top-N memory consumers are stored;
* retention/compaction and baseline refresh run hourly;
* no LLM calls and no temperature polling happen in the background.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from boostai.config.settings import Settings
from boostai.core.baselines import BaselineEngine
from boostai.core.history import History
from boostai.core.models import SystemSnapshot
from boostai.core.sampler import SystemSampler
from boostai.core.trend import mean
from boostai.database.repository import ProcessSampleRow, Repository, SystemSampleRow
from boostai.utils import winapi

log = logging.getLogger(__name__)

MAINTENANCE_INTERVAL_S = 3600


class Monitor:
    def __init__(self, sampler: SystemSampler, history: History, repo: Repository | None,
                 settings_provider: Callable[[], Settings], baselines: BaselineEngine | None = None) -> None:
        self.sampler = sampler
        self.history = history
        self.repo = repo
        self.settings_provider = settings_provider
        self.baselines = baselines
        self.latest: SystemSnapshot | None = None
        self._listeners: list[Callable[[SystemSnapshot], None]] = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._background = False
        self._fast_until = 0.0
        self._pending_system: list[SystemSnapshot] = []
        self.sample_count = 0

    # ---------------------------------------------------------------- control
    def add_listener(self, fn: Callable[[SystemSnapshot], None]) -> None:
        self._listeners.append(fn)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="boostai-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        self._flush(force=True)

    def set_background(self, background: bool) -> None:
        """Hidden window => slower sampling."""
        self._background = background
        self._wake.set()

    def boost_sampling(self, seconds: float) -> None:
        """Deep scan: sample processes at the fastest rate for a while."""
        self._fast_until = time.time() + seconds
        self._wake.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------- loop
    def _intervals(self) -> tuple[float, float]:
        m = self.settings_provider().monitor
        if time.time() < self._fast_until:
            return m.system_interval_s, max(3.0, min(5.0, m.process_interval_s))
        if self._background:
            return m.background_interval_s, max(m.background_interval_s, m.process_interval_s)
        return m.system_interval_s, m.process_interval_s

    def _run(self) -> None:
        next_proc = 0.0
        last_persist = time.time()
        last_maintenance = 0.0
        while not self._stop.is_set():
            started = time.time()
            sys_iv, proc_iv = self._intervals()
            try:
                include_proc = started >= next_proc
                snap = self.sampler.snapshot(include_processes=include_proc)
                if include_proc:
                    self.history.add_processes(snap.timestamp, snap.processes)
                    next_proc = started + proc_iv
                elif self.latest is not None:
                    snap.processes = self.latest.processes  # reuse the last process list for display
                self.history.add_system_snapshot(snap, fullscreen=winapi.foreground_is_fullscreen())
                self.latest = snap
                self.sample_count += 1
                self._pending_system.append(snap)
                for fn in list(self._listeners):
                    try:
                        fn(snap)
                    except Exception:
                        log.exception("Monitor listener failed")
                settings = self.settings_provider()
                if started - last_persist >= settings.monitor.persist_interval_s:
                    self._flush()
                    last_persist = started
                if started - last_maintenance >= MAINTENANCE_INTERVAL_S:
                    self._maintenance(settings)
                    last_maintenance = started
            except Exception:
                log.exception("Monitor iteration failed")
            elapsed = time.time() - started
            self._wake.wait(max(0.2, sys_iv - elapsed))
            self._wake.clear()

    # ------------------------------------------------------------ persistence
    def _flush(self, force: bool = False) -> None:
        if self.repo is None or not self._pending_system:
            return
        pending, self._pending_system = self._pending_system, []
        try:
            idle = [s.user_idle_seconds for s in pending if s.user_idle_seconds is not None]
            commit = [s.memory.commit_percent for s in pending if s.memory.commit_percent is not None]
            row = SystemSampleRow(
                timestamp=pending[-1].timestamp,
                cpu=mean([s.cpu.total_percent for s in pending]),
                ram_percent=mean([s.memory.percent for s in pending]),
                available=int(mean([s.memory.available for s in pending])),
                commit_percent=mean(commit) if commit else None,
                disk_read_bps=mean([s.disk_activity.read_bps for s in pending]),
                disk_write_bps=mean([s.disk_activity.write_bps for s in pending]),
                process_count=int(round(mean([s.process_count for s in pending]))),
                user_idle=min(idle) if idle else None,
            )
            self.repo.insert_system_samples([row])
            latest = pending[-1]
            top_n = self.settings_provider().monitor.top_processes_persisted
            top = sorted(latest.processes, key=lambda p: p.private, reverse=True)[:top_n]
            self.repo.insert_process_samples([
                ProcessSampleRow(latest.timestamp, p.name, p.pid, p.create_time, p.private, p.rss, p.cpu_percent,
                                 p.threads, p.handles)
                for p in top
            ])
        except Exception:
            log.exception("Persisting samples failed")

    def _maintenance(self, settings: Settings) -> None:
        if self.repo is None:
            return
        try:
            r = settings.retention
            stats = self.repo.run_maintenance(r.raw_system_samples_hours, r.compacted_system_samples_days,
                                              r.process_samples_days, r.scans_days)
            log.info("Maintenance: %s", stats)
            if self.baselines is not None:
                self.baselines.refresh()
        except Exception:
            log.exception("Maintenance failed")
