"""Run engine calls off the UI thread."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    """``fn(*args, progress=callback, **kwargs)`` if ``with_progress`` else ``fn(*args, **kwargs)``."""

    def __init__(self, fn: Callable[..., Any], *args: Any, with_progress: bool = False, **kwargs: Any) -> None:
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.with_progress = with_progress
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            if self.with_progress:
                self.kwargs["progress"] = lambda pct, msg: self.signals.progress.emit(int(pct), str(msg))
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            log.error("Background task failed: %s\n%s", exc, traceback.format_exc())
            self._emit(self.signals.error, f"{type(exc).__name__}: {exc}")
        else:
            self._emit(self.signals.result, result)
        finally:
            self._emit(self.signals.finished)

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass  # the receiving window was closed while the task was running


_active: set[Worker] = set()


def run_async(fn: Callable[..., Any], *args: Any, on_result=None, on_error=None, on_progress=None,
              on_finished=None, with_progress: bool = False, **kwargs: Any) -> Worker:
    worker = Worker(fn, *args, with_progress=with_progress, **kwargs)
    if on_result:
        worker.signals.result.connect(on_result)
    if on_error:
        worker.signals.error.connect(on_error)
    if on_progress:
        worker.signals.progress.connect(on_progress)
    if on_finished:
        worker.signals.finished.connect(on_finished)
    _active.add(worker)
    worker.signals.finished.connect(lambda: _active.discard(worker))
    QThreadPool.globalInstance().start(worker)
    return worker
