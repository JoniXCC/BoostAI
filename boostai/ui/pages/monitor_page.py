"""Real-time charts: CPU, RAM, available memory, disk activity and top processes."""

from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QVBoxLayout

from boostai.core.models import GB, MB
from boostai.ui import theme
from boostai.ui.pages.base import Page
from boostai.ui.widgets import card, label, page_header

SERIES_COLORS = ["#4f8cff", "#3ecf8e", "#f5c542", "#ff8a3d", "#c77dff", "#5fd3f3"]


def _plot(title: str, y_label: str, y_range: tuple[float, float] | None = None) -> pg.PlotWidget:
    w = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
    w.setTitle(title, size="10pt")
    w.setLabel("left", y_label)
    w.showGrid(x=True, y=True, alpha=0.15)
    w.setMinimumHeight(210)
    w.setMouseEnabled(x=False, y=False)
    w.setMenuEnabled(False)
    if y_range:
        w.setYRange(*y_range)
    return w


class MonitorPage(Page):
    key = "monitor"
    title = "Monitor"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        head = QHBoxLayout()
        head.addWidget(page_header("Real-time monitor", "Live history kept in memory (bounded) and summarised to the "
                                   "database once a minute. Sampling slows down automatically when BoostAI is hidden."), 1)
        self.window = QComboBox()
        for text, minutes in (("Last 5 minutes", 5), ("Last 15 minutes", 15), ("Last hour", 60), ("Last 2 hours", 120)):
            self.window.addItem(text, minutes)
        self.window.setCurrentIndex(1)
        self.window.currentIndexChanged.connect(lambda _i: self.redraw())
        head.addWidget(self.window)
        self.body.addLayout(head)
        self.info = label("", muted=True)
        self.body.addWidget(self.info)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cpu_plot = _plot("CPU usage", "%", (0, 100))
        self.ram_plot = _plot("Memory usage", "%", (0, 100))
        self.avail_plot = _plot("Available memory", "GB")
        self.disk_plot = _plot("Disk activity", "MB/s")
        self.top_plot = _plot("Top processes (private memory)", "GB")
        self.top_plot.addLegend(offset=(8, 4))
        self.disk_plot.addLegend(offset=(8, 4))
        for i, w in enumerate((self.cpu_plot, self.ram_plot, self.avail_plot, self.disk_plot)):
            c = card()
            lay = QVBoxLayout(c)
            lay.setContentsMargins(8, 8, 8, 8)
            lay.addWidget(w)
            grid.addWidget(c, i // 2, i % 2)
        self.body.addLayout(grid)
        c = card()
        lay = QVBoxLayout(c)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.top_plot)
        self.top_plot.setMinimumHeight(260)
        self.body.addWidget(c)
        self.body.addStretch(1)

        self.cpu_curve = self.cpu_plot.plot()
        self.ram_curve = self.ram_plot.plot()
        self.avail_curve = self.avail_plot.plot()
        self.read_curve = self.disk_plot.plot(name="Read")
        self.write_curve = self.disk_plot.plot(name="Write")
        self.top_curves: dict[str, pg.PlotDataItem] = {}
        self.refresh_theme()
        bridge.snapshot.connect(lambda _s: self.redraw() if self.isVisible() else None)

    def refresh_theme(self) -> None:
        p = theme.current()
        for w in (self.cpu_plot, self.ram_plot, self.avail_plot, self.disk_plot, self.top_plot):
            w.setBackground(p.surface)
            for axis in ("left", "bottom"):
                w.getAxis(axis).setTextPen(p.muted)
                w.getAxis(axis).setPen(p.border)

    def on_show(self) -> None:
        self.redraw()

    def redraw(self) -> None:
        p = theme.current()
        minutes = self.window.currentData()
        since = time.time() - minutes * 60
        pts = self.engine.history.system_points(since=since)
        m = self.engine.settings.monitor
        self.info.setText(f"{len(pts)} samples · system every {m.system_interval_s:g} s, processes every "
                          f"{m.process_interval_s:g} s · BoostAI itself: {self._self_usage()}")
        if not pts:
            return
        xs = [pt.t for pt in pts]
        self.cpu_curve.setData(xs, [pt.cpu for pt in pts], pen=pg.mkPen(p.accent, width=2))
        self.ram_curve.setData(xs, [pt.ram_percent for pt in pts], pen=pg.mkPen(p.good, width=2))
        self.avail_curve.setData(xs, [pt.available / GB for pt in pts], pen=pg.mkPen(p.info, width=2))
        self.read_curve.setData(xs, [pt.disk_read_bps / MB for pt in pts], pen=pg.mkPen(p.accent, width=2))
        self.write_curve.setData(xs, [pt.disk_write_bps / MB for pt in pts], pen=pg.mkPen(p.warn, width=2))

        # Top 6 applications by current combined private memory
        groups = []
        for lname in self.engine.history.group_names():
            series = [s for s in self.engine.history.group_series(lname) if s[0] >= since]
            if series:
                groups.append((series[-1][1], lname, series))
        groups.sort(reverse=True)
        wanted = {lname for _v, lname, _s in groups[:6]}
        for lname in list(self.top_curves):
            if lname not in wanted:
                self.top_plot.removeItem(self.top_curves.pop(lname))
                self.top_plot.plotItem.legend.removeItem(self.engine.history.display_name(lname))
        for idx, (_v, lname, series) in enumerate(groups[:6]):
            curve = self.top_curves.get(lname)
            if curve is None:
                curve = self.top_plot.plot(name=self.engine.history.display_name(lname))
                self.top_curves[lname] = curve
            curve.setData([s[0] for s in series], [s[1] / GB for s in series],
                          pen=pg.mkPen(SERIES_COLORS[idx % len(SERIES_COLORS)], width=2))

    def _self_usage(self) -> str:
        import os

        import psutil

        try:
            proc = psutil.Process(os.getpid())
            return f"{proc.memory_info().rss / MB:.0f} MB RAM"
        except psutil.Error:
            return "n/a"
