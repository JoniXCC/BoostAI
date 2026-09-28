"""History: health trend, scans, audit log of actions with Undo, before/after comparisons."""

from __future__ import annotations

import html
import time

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from boostai.core.models import fmt_bytes
from boostai.ui import theme
from boostai.ui.dialogs import status_color
from boostai.ui.pages.base import Page
from boostai.ui.widgets import card, label, page_header
from boostai.ui.workers import run_async


def _ts(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


class HistoryPage(Page):
    key = "history"
    title = "History"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self.body.addWidget(page_header("History", "Every scan, every change BoostAI made (or refused to make), and "
                                        "every before/after measurement."))
        c = card()
        cl = QVBoxLayout(c)
        cl.setContentsMargins(8, 8, 8, 8)
        self.trend = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.trend.setTitle("Health score over time", size="10pt")
        self.trend.setYRange(0, 100)
        self.trend.setMinimumHeight(190)
        self.trend.showGrid(x=True, y=True, alpha=0.15)
        self.trend.setMouseEnabled(x=False, y=False)
        cl.addWidget(self.trend)
        self.body.addWidget(c)

        tabs = QTabWidget()
        # Actions
        aw = QWidget()
        al = QVBoxLayout(aw)
        self.actions = self._table(["Time", "Action", "Target", "Result", "Reversibility", "Undo status", "Details"])
        self.actions.itemSelectionChanged.connect(self._update_undo)
        al.addWidget(self.actions, 1)
        arow = QHBoxLayout()
        self.undo_note = label("", muted=True, wrap=True)
        arow.addWidget(self.undo_note, 1)
        self.undo_btn = QPushButton("Undo change")
        self.undo_btn.setObjectName("Primary")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo)
        arow.addWidget(self.undo_btn)
        al.addLayout(arow)
        tabs.addTab(aw, "Changes (audit log)")
        # Scans
        sw = QWidget()
        sl = QVBoxLayout(sw)
        self.scans = self._table(["Time", "Type", "Health", "CPU", "RAM", "Pressure", "Processes", "Issues"])
        sl.addWidget(self.scans)
        tabs.addTab(sw, "Scans")
        # Comparisons
        cw = QWidget()
        cvl = QVBoxLayout(cw)
        self.comparisons = QTextBrowser()
        cvl.addWidget(self.comparisons)
        tabs.addTab(cw, "Before / after")
        tabs.setMinimumHeight(420)
        self.body.addWidget(tabs, 1)
        self._records: list[dict] = []
        bridge.actions_changed.connect(self.on_show)
        bridge.scan_finished.connect(lambda _r: self.on_show())

    @staticmethod
    def _table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
        return t

    def on_show(self) -> None:
        repo = self.engine.repo
        run_async(lambda: (repo.recent_scans(200), repo.recent_actions(300), repo.recent_comparisons(30)),
                  on_result=self._fill)

    def _fill(self, data) -> None:
        scans, actions, comparisons = data
        p = theme.current()
        self.trend.setBackground(p.surface)
        pts = sorted((s["timestamp"], s["health_score"]) for s in scans if s["health_score"] is not None)
        self.trend.clear()
        if pts:
            self.trend.plot([x for x, _ in pts], [y for _, y in pts], pen=pg.mkPen(p.accent, width=2),
                            symbol="o", symbolSize=6, symbolBrush=p.accent)

        self.scans.setRowCount(len(scans))
        for r, s in enumerate(scans):
            vals = [_ts(s["timestamp"]), s["kind"], f"{s['health_score']:.0f}" if s["health_score"] is not None else "-",
                    f"{s['cpu_usage']:.0f}%" if s["cpu_usage"] is not None else "-",
                    f"{s['memory_usage']:.0f}%" if s["memory_usage"] is not None else "-",
                    s["memory_pressure"] or "-", str(s["process_count"] or "-"), str(s["issue_count"] or 0)]
            for c, v in enumerate(vals):
                self.scans.setItem(r, c, QTableWidgetItem(v))

        self._records = actions
        self.actions.setRowCount(len(actions))
        for r, a in enumerate(actions):
            if a.get("rolled_back"):
                undo = f"Undone {_ts(a.get('rollback_timestamp'))}"
            elif a.get("rollback_result") == "FAILED":
                undo = "Undo failed"
            elif a.get("rollback_available"):
                undo = "Can be undone"
            else:
                undo = "Not reversible" if a.get("reversibility") in ("NOT_REVERSIBLE", "NOT_APPLICABLE") else "-"
            vals = [_ts(a["timestamp"]), a["action_id"].replace("_", " ").title(), a.get("target") or "",
                    a["result"].replace("_", " ").title(), (a.get("reversibility") or "-").replace("_", " ").title(),
                    undo, a.get("message") or ""]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c == 3:
                    cell.setForeground(QColor(status_color(a["result"])))
                if c == 6:
                    cell.setToolTip(v)
                self.actions.setItem(r, c, cell)
        self._update_undo()

        e = html.escape
        parts = []
        for comp in comparisons:
            rows = "".join(f"<tr><td>{e(r['metric'])}</td><td>{e(r['before'])}</td><td>{e(r['after'])}</td>"
                           f"<td>{e(r['verdict'])}</td></tr>" for r in comp["summary"]["rows"])
            hl = ", ".join(comp["summary"]["highlights"]) or "no meaningful system-wide change"
            parts.append(f"<h3>{_ts(comp['timestamp'])}</h3><p><b>{e(hl)}</b></p>"
                         f"<table cellpadding='4'><tr><th align='left'>Metric</th><th align='left'>Before</th>"
                         f"<th align='left'>After</th><th align='left'>Result</th></tr>{rows}</table>")
        self.comparisons.setHtml("".join(parts) or "<p>No optimizations applied yet.</p>")

    def _current(self) -> dict | None:
        rows = self.actions.selectionModel().selectedRows()
        return self._records[rows[0].row()] if rows else None

    def _update_undo(self) -> None:
        rec = self._current()
        if rec is None:
            self.undo_btn.setEnabled(False)
            self.undo_note.setText("Select a change to see whether it can be undone.")
            return
        ok, reason = self.engine.rollback_manager.can_rollback(rec)
        self.undo_btn.setEnabled(ok)
        prev = rec.get("previous_state") or {}
        detail = ""
        if isinstance(prev, dict) and "enabled" in prev:
            detail = f" Previous state: {'enabled' if prev['enabled'] else 'disabled'}."
        elif isinstance(prev, dict) and prev.get("name") and rec["action_id"] == "CHANGE_POWER_PLAN":
            detail = f" Previous plan: {prev['name']}."
        elif isinstance(prev, dict) and "private_bytes" in prev:
            detail = f" Memory before: {fmt_bytes(prev['private_bytes'])}."
        self.undo_note.setText(("Reversible." if ok else reason) + detail)

    def undo(self) -> None:
        rec = self._current()
        if rec is None:
            return
        if QMessageBox.question(self, "Undo change", f"Restore the previous state of '{rec.get('target')}'?") != QMessageBox.Yes:
            return
        self.undo_btn.setEnabled(False)

        def done(result):
            (QMessageBox.information if result.success else QMessageBox.warning)(self, "Undo", result.message)
            self.bridge.actions_changed.emit()

        run_async(self.engine.undo, rec["id"], on_result=done,
                  on_error=lambda e: QMessageBox.critical(self, "Undo failed", e))
