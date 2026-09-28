"""Main dashboard: live status, health score, issues and recommended actions."""

from __future__ import annotations

import html
import time

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from boostai.core.models import fmt_bytes, fmt_duration
from boostai.ui import theme
from boostai.ui.pages.base import Page
from boostai.ui.widgets import CategoryBar, ScoreRing, StatCard, card, label, page_header, severity_badge


class DashboardPage(Page):
    key = "dashboard"
    title = "Dashboard"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        head = QHBoxLayout()
        head.addWidget(page_header("System status", "Live measurements from this PC. Nothing is changed without "
                                   "your approval."), 1)
        self.scan_btn = QPushButton("Run quick scan")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.clicked.connect(lambda: bridge.navigate.emit("scan:quick"))
        head.addWidget(self.scan_btn, alignment=Qt.AlignTop)
        self.body.addLayout(head)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cpu = StatCard("CPU")
        self.ram = StatCard("Memory")
        self.disk = StatCard("System drive")
        self.health = StatCard("Health", show_bar=True)
        for i, c in enumerate((self.cpu, self.ram, self.disk, self.health)):
            grid.addWidget(c, 0, i)
        self.body.addLayout(grid)

        row = QHBoxLayout()
        row.setSpacing(12)
        # Health score card
        score_card = card()
        sl = QHBoxLayout(score_card)
        sl.setContentsMargins(16, 16, 16, 16)
        self.ring = ScoreRing()
        sl.addWidget(self.ring, alignment=Qt.AlignTop)
        cats = QVBoxLayout()
        cats.addWidget(label("Performance health", object_name="SectionTitle"))
        self.cat_bars: dict[str, CategoryBar] = {}
        for name in ("Memory", "CPU", "Responsiveness", "Disk capacity", "Startup", "Background load"):
            bar = CategoryBar(name)
            self.cat_bars[name] = bar
            cats.addWidget(bar)
        self.score_note = label("Run a scan to calculate the health score. It is a transparent summary of measured "
                                "factors, not an industry benchmark.", muted=True, wrap=True)
        cats.addWidget(self.score_note)
        sl.addLayout(cats, 1)
        row.addWidget(score_card, 3)

        # Live mini charts
        charts = card()
        cl = QVBoxLayout(charts)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.addWidget(label("Last 5 minutes", object_name="SectionTitle"))
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(170)
        self.plot.setYRange(0, 100)
        self.plot.showGrid(x=False, y=True, alpha=0.15)
        self.plot.addLegend(offset=(8, 4))
        self.plot.getPlotItem().hideAxis("bottom")
        self.cpu_curve = self.plot.plot(name="CPU %")
        self.ram_curve = self.plot.plot(name="RAM %")
        cl.addWidget(self.plot)
        row.addWidget(charts, 2)
        self.body.addLayout(row)

        lower = QHBoxLayout()
        lower.setSpacing(12)
        issues_card = card()
        il = QVBoxLayout(issues_card)
        il.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        self.issues_title = label("Potential issues", object_name="SectionTitle")
        top.addWidget(self.issues_title, 1)
        view = QPushButton("View all")
        view.clicked.connect(lambda: bridge.navigate.emit("issues"))
        top.addWidget(view)
        il.addLayout(top)
        self.issues_list = QLabel("No scan yet.")
        self.issues_list.setTextFormat(Qt.RichText)
        self.issues_list.setWordWrap(True)
        self.issues_list.setAlignment(Qt.AlignTop)
        il.addWidget(self.issues_list, 1)
        lower.addWidget(issues_card, 3)

        rec_card = card()
        rl = QVBoxLayout(rec_card)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.addWidget(label("Recommended actions", object_name="SectionTitle"))
        self.rec_text = label("Run a scan to get recommendations.", muted=True, wrap=True)
        rl.addWidget(self.rec_text)
        rl.addStretch(1)
        self.review_btn = QPushButton("Review recommended changes")
        self.review_btn.setObjectName("Primary")
        self.review_btn.setEnabled(False)
        self.review_btn.clicked.connect(lambda: bridge.navigate.emit("optimizer"))
        rl.addWidget(self.review_btn)
        lower.addWidget(rec_card, 2)
        self.body.addLayout(lower)

        sysinfo = card()
        sil = QVBoxLayout(sysinfo)
        sil.setContentsMargins(16, 12, 16, 12)
        sil.addWidget(label("This PC", object_name="SectionTitle"))
        self.sysinfo_text = label("", muted=True, wrap=True)
        sil.addWidget(self.sysinfo_text)
        self.body.addWidget(sysinfo)
        self.body.addStretch(1)

        bridge.snapshot.connect(self.on_snapshot)
        bridge.scan_finished.connect(self.on_scan)
        self._fill_sysinfo()
        self.refresh_theme()

    def _fill_sysinfo(self) -> None:
        try:
            info = self.engine.system_info()
        except Exception:
            return
        gpus = ", ".join(g.name + (f" ({fmt_bytes(g.memory_bytes)})" if g.memory_bytes else "") for g in info.gpus) or "n/a"
        disks = ", ".join(f"{d.name} ({d.media_type}, {d.health})" for d in info.storage) or "n/a"
        self.sysinfo_text.setText(
            f"{info.os_name} {info.os_version} (build {info.os_build}) · {info.architecture} · {info.hostname}\n"
            f"{info.cpu_model} · {info.physical_cores} cores / {info.logical_cores} threads · "
            f"{fmt_bytes(info.total_ram)} RAM\nGPU: {gpus}\nStorage: {disks}\n"
            f"Uptime: {fmt_duration(info.uptime_seconds)} · "
            f"{'Running as administrator' if info.is_admin else 'Running as standard user (elevates per action)'}")

    def on_snapshot(self, snap) -> None:
        p = theme.current()
        cpu = snap.cpu.total_percent
        self.cpu.set(f"{cpu:.0f}%", f"{len(snap.cpu.per_core)} logical cores", cpu,
                     p.good if cpu < 60 else p.warn if cpu < 85 else p.bad)
        m = snap.memory
        self.ram.set(f"{m.percent:.0f}%", f"{fmt_bytes(m.used)} of {fmt_bytes(m.total)} · pressure "
                     f"{snap.memory_pressure.value.lower()}", m.percent,
                     p.good if m.percent < 75 else p.warn if m.percent < 90 else p.bad)
        d = snap.system_disk
        if d:
            self.disk.set(f"{d.percent:.0f}%", f"{fmt_bytes(d.free)} free on {d.mountpoint}", d.percent,
                          p.good if d.percent < 85 else p.warn if d.percent < 93 else p.bad)
        points = self.engine.history.system_points(since=time.time() - 300)
        if points:
            xs = [pt.t - points[-1].t for pt in points]
            self.cpu_curve.setData(xs, [pt.cpu for pt in points], pen=pg.mkPen(p.accent, width=2))
            self.ram_curve.setData(xs, [pt.ram_percent for pt in points], pen=pg.mkPen(p.good, width=2))

    def on_scan(self, result) -> None:
        s = result.score
        self.ring.set_score(s.overall, s.label)
        self.health.set(f"{s.overall:.0f}", s.label, s.overall, theme.score_color(s.overall))
        for name, bar in self.cat_bars.items():
            c = s.categories.get(name)
            bar.setVisible(c is not None)
            if c:
                bar.set(c.score, "\n".join(c.details))
        self.score_note.setText(f"Scanned {time.strftime('%H:%M', time.localtime(result.started))}. Hover a bar to "
                                "see how it was calculated.")
        issues = [i for i in result.issues if i.severity.value != "INFO"]
        self.issues_title.setText(f"{len(issues)} issue{'s' if len(issues) != 1 else ''} detected")
        if not result.issues:
            self.issues_list.setText("No problems detected.")
        else:
            rows = [f"{severity_badge(i.severity.value)}&nbsp; {html.escape(i.title)}" for i in result.issues[:6]]
            self.issues_list.setText("<br><br>".join(rows))
        n = sum(len(i.proposals) for i in result.issues)
        self.review_btn.setEnabled(n > 0)
        self.rec_text.setText(f"{n} safe change{'s' if n != 1 else ''} can be reviewed. Nothing is applied until you "
                              "approve it." if n else "No automatic changes are recommended. See the issues for "
                              "manual advice.")

    def refresh_theme(self) -> None:
        p = theme.current()
        self.plot.setBackground(p.surface)
        for axis in ("left", "bottom"):
            self.plot.getAxis(axis).setTextPen(p.muted)
            self.plot.getAxis(axis).setPen(p.border)
