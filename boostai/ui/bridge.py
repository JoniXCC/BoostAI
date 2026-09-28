"""Qt-side facade over the engine: thread-safe signals for monitor samples and scan results."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from boostai.core.engine import BoostEngine


class EngineBridge(QObject):
    snapshot = Signal(object)        # SystemSnapshot, throttled to ~1 Hz, delivered on the UI thread
    scan_finished = Signal(object)   # ScanResult
    actions_changed = Signal()       # an action or rollback was recorded
    settings_changed = Signal()
    navigate = Signal(str)           # page key
    notify = Signal(str, str)        # title, message (tray notification)

    def __init__(self, engine: BoostEngine) -> None:
        super().__init__()
        self.engine = engine
        self._last_emit = 0.0
        engine.monitor.add_listener(self._on_sample)

    def _on_sample(self, snap) -> None:  # monitor thread
        now = time.monotonic()
        if now - self._last_emit >= 0.9:
            self._last_emit = now
            self.snapshot.emit(snap)  # queued to the UI thread automatically
