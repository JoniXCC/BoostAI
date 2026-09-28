"""Quick scan and Deep Scan."""

from __future__ import annotations

import html
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from boostai.core.models import fmt_bytes
from boostai.ui import theme
from boostai.ui.pages.base import Page
from boostai.ui.widgets import card, label, page_header, severity_badge
from boostai.ui.workers import run_async


class ScanPage(Page):
    key = "scan"
    title = "Scan"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self._cancel: threading.Event | None = None
        self.body.addWidget(page_header(
            "Scan", "A quick scan measures the system now and analyses the monitoring history. A deep scan observes "
                    "memory and CPU trends for longer to find leaks and repeated spikes. Scans never change anything."))

        row = QHBoxLayout()
        row.setSpacing(12)
        quick = card()
        ql = QVBoxLayout(quick)
        ql.setContentsMargins(16, 14, 16, 14)
        ql.addWidget(label("Quick scan", object_name="SectionTitle"))
        ql.addWidget(label("About 5-20 seconds. Uses the monitor's recent history for trends.", muted=True, wrap=True))
        self.quick_btn = QPushButton("Run quick scan")
        self.quick_btn.setObjectName("Primary")
        self.quick_btn.clicked.connect(self.start_quick)
        ql.addWidget(self.quick_btn, alignment=Qt.AlignLeft)
        row.addWidget(quick, 1)

        deep = card()
        dl = QVBoxLayout(deep)
        dl.setContentsMargins(16, 14, 16, 14)
        dl.addWidget(label("Deep scan", object_name="SectionTitle"))
        dl.addWidget(label("Samples processes more often for the chosen period, then analyses leaks, build-up and "
                           "CPU spikes. Keep using your PC normally; it makes no changes.", muted=True, wrap=True))
        drow = QHBoxLayout()
        self.duration = QComboBox()
        for minutes in (5, 10, 20, 30, 60):
            self.duration.addItem(f"{minutes} minutes", minutes)
        self.duration.setCurrentIndex(1)
        drow.addWidget(self.duration)
        self.deep_btn = QPushButton("Start deep scan")
        self.deep_btn.clicked.connect(self.start_deep)
        drow.addWidget(self.deep_btn)
        drow.addStretch(1)
        dl.addLayout(drow)
        row.addWidget(deep, 1)
        self.body.addLayout(row)

        prog = card()
        pl = QVBoxLayout(prog)
        pl.setContentsMargins(16, 14, 16, 14)
        self.status = label("Ready.", muted=True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        prow = QHBoxLayout()
        prow.addWidget(self.bar, 1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        prow.addWidget(self.cancel_btn)
        pl.addWidget(self.status)
        pl.addLayout(prow)
        self.body.addWidget(prog)

        self.result_card = card()
        rl = QVBoxLayout(self.result_card)
        rl.setContentsMargins(16, 14, 16, 14)
        self.result_text = QLabel()
        self.result_text.setTextFormat(Qt.RichText)
        self.result_text.setWordWrap(True)
        rl.addWidget(self.result_text)
        brow = QHBoxLayout()
        issues_btn = QPushButton("View issues and evidence")
        issues_btn.setObjectName("Primary")
        issues_btn.clicked.connect(lambda: bridge.navigate.emit("issues"))
        opt_btn = QPushButton("Review recommended changes")
        opt_btn.clicked.connect(lambda: bridge.navigate.emit("optimizer"))
        brow.addWidget(issues_btn)
        brow.addWidget(opt_btn)
        brow.addStretch(1)
        rl.addLayout(brow)
        self.result_card.setVisible(False)
        self.body.addWidget(self.result_card)
        self.body.addStretch(1)

    # ----------------------------------------------------------------- run
    def _busy(self, busy: bool) -> None:
        self.quick_btn.setEnabled(not busy)
        self.deep_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)

    def start_quick(self) -> None:
        if not self.quick_btn.isEnabled():
            return
        self._cancel = threading.Event()
        self._busy(True)
        run_async(self.engine.run_scan, "quick", cancel=self._cancel, with_progress=True,
                  on_progress=self._progress, on_result=self._done, on_error=self._error)

    def start_deep(self) -> None:
        self._cancel = threading.Event()
        self._busy(True)
        minutes = self.duration.currentData()
        run_async(self.engine.deep_scan, minutes, cancel=self._cancel, with_progress=True,
                  on_progress=self._progress, on_result=self._done, on_error=self._error)

    def cancel(self) -> None:
        if self._cancel:
            self._cancel.set()
            self.status.setText("Cancelling...")

    def _progress(self, pct: int, msg: str) -> None:
        self.bar.setValue(pct)
        self.status.setText(msg)

    def _error(self, msg: str) -> None:
        self._busy(False)
        self.bar.setValue(0)
        self.status.setText("Scan cancelled." if "ScanCancelled" in msg else f"Scan failed: {msg}")

    def _done(self, result) -> None:
        self._busy(False)
        self.bar.setValue(100)
        p = theme.current()
        s = result.score
        self.status.setText(f"{result.kind.title()} scan finished in {result.duration:.1f} s.")
        snap = result.snapshot
        issue_rows = "".join(f"<li>{severity_badge(i.severity.value)} {html.escape(i.title)}</li>" for i in result.issues)
        temps = snap.temperatures
        temp_line = ", ".join(f"{r.sensor}: {r.celsius:.0f} °C" for r in temps.readings) or temps.message
        self.result_text.setText(
            f"<h2 style='color:{theme.score_color(s.overall)}'>Health {s.overall:.0f}/100 – {s.label}</h2>"
            f"<p>CPU {snap.cpu.total_percent:.0f}% · RAM {snap.memory.percent:.0f}% "
            f"({fmt_bytes(snap.memory.used)} / {fmt_bytes(snap.memory.total)}) · memory pressure "
            f"{snap.memory_pressure.value} · {snap.process_count} processes</p>"
            f"<p style='color:{p.muted}'>Temperatures: {html.escape(temp_line)}</p>"
            f"<h3>{len(result.issues)} finding(s)</h3><ul>{issue_rows or '<li>No problems detected.</li>'}</ul>")
        self.result_card.setVisible(True)
        self.bridge.scan_finished.emit(result)
