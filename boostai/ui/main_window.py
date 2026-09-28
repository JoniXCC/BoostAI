"""Main window: sidebar navigation, pages, status bar, system tray and background watch."""

from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from boostai import __version__
from boostai.config.logging_config import log_event
from boostai.core.engine import BoostEngine
from boostai.ui import theme
from boostai.ui.bridge import EngineBridge
from boostai.ui.pages.cleanup_page import CleanupPage
from boostai.ui.pages.dashboard import DashboardPage
from boostai.ui.pages.gaming_page import GamingPage
from boostai.ui.pages.history_page import HistoryPage
from boostai.ui.pages.issues_page import IssuesPage
from boostai.ui.pages.monitor_page import MonitorPage
from boostai.ui.pages.optimizer_page import OptimizerPage
from boostai.ui.pages.process_page import ProcessPage
from boostai.ui.pages.scan_page import ScanPage
from boostai.ui.pages.settings_page import SettingsPage
from boostai.ui.pages.startup_page import StartupPage
from boostai.ui.widgets import app_icon, label
from boostai.ui.workers import run_async
from boostai.utils import winapi

WATCH_INTERVAL_MS = 5 * 60 * 1000
NOTIFY_COOLDOWN_S = 3600

NAV = [
    ("dashboard", "Dashboard"), ("scan", "Scan"), ("issues", "Issues"), ("optimizer", "Optimizer"),
    ("processes", "Processes"), ("monitor", "Monitor"), ("startup", "Startup & services"), ("cleanup", "Cleanup"),
    ("gaming", "Gaming mode"), ("history", "History"), ("settings", "Settings"),
]


class MainWindow(QMainWindow):
    def __init__(self, engine: BoostEngine) -> None:
        super().__init__()
        self.engine = engine
        self.bridge = EngineBridge(engine)
        self._really_quit = False
        self._notified: dict[str, float] = {}
        self._auto_scanned = False
        self.setWindowTitle("BoostAI – PC Performance Booster")
        self.setWindowIcon(app_icon())
        self.resize(1320, 860)
        self.setMinimumSize(1060, 680)
        pg.setConfigOptions(antialias=True)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(12, 18, 12, 14)
        sl.setSpacing(3)
        sl.addWidget(label("BoostAI", object_name="AppTitle"))
        sl.addWidget(label("Measure · Explain · Verify", object_name="AppSubtitle"))
        sl.addSpacing(14)
        self.stack = QStackedWidget()
        self.pages = {}
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        page_classes = [DashboardPage, ScanPage, IssuesPage, OptimizerPage, ProcessPage, MonitorPage, StartupPage,
                        CleanupPage, GamingPage, HistoryPage, SettingsPage]
        self.nav_buttons = {}
        for (key, text), cls in zip(NAV, page_classes):
            page = cls(self.bridge)
            self.pages[key] = page
            self.stack.addWidget(page)
            btn = QPushButton(text.replace("&", "&&"))
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            sl.addWidget(btn)
        sl.addStretch(1)
        self.side_status = label("", muted=True, wrap=True)
        sl.addWidget(self.side_status)
        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)

        self.bridge.navigate.connect(self.navigate)
        self.bridge.settings_changed.connect(self.update_status)
        self.bridge.snapshot.connect(self._on_first_snapshot)
        self._setup_tray()
        self.update_status()
        self.show_page("dashboard")
        if engine.monitor.latest:
            self.bridge.snapshot.emit(engine.monitor.latest)

        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self._watch)
        self.watch_timer.start(WATCH_INTERVAL_MS)
        log_event("app_started", version=__version__, admin=winapi.is_admin())

    # ------------------------------------------------------------ navigation
    def show_page(self, key: str) -> None:
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.nav_buttons[key].setChecked(True)
        page.on_show()

    def navigate(self, target: str) -> None:
        if target == "scan:quick":
            self.show_page("scan")
            self.pages["scan"].start_quick()
        elif target == "scan:quick-silent":
            self._background_scan()
        elif target in self.pages:
            self.show_page(target)

    def _background_scan(self) -> None:
        run_async(self.engine.run_scan, "quick", on_result=self.bridge.scan_finished.emit)

    def _on_first_snapshot(self, _snap) -> None:
        # One automatic, read-only quick scan shortly after start-up so the dashboard has a health score.
        if not self._auto_scanned and self.engine.monitor.sample_count >= 5:
            self._auto_scanned = True
            self._background_scan()

    # ---------------------------------------------------------------- status
    def update_status(self) -> None:
        s = self.engine.settings
        ai = {"none": "off", "ollama": "local (Ollama)"}.get(s.ai.provider.value, s.ai.provider.value.title())
        if s.ai.provider.is_cloud:
            ai += " – blocked (local-only)" if s.ai.local_only else " – cloud"
        admin = "Administrator" if winapi.is_admin() else "Standard user"
        gaming = "\nGaming mode active" if s.active_gaming_session else ""
        self.side_status.setText(f"Mode: {s.mode.value.title()}\nAI: {ai}\n{admin} (elevates per action){gaming}\n"
                                 f"v{__version__}")

    def apply_theme(self, name: str) -> None:
        theme.apply_theme(QApplication.instance(), name)
        for page in self.pages.values():
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()
        self.update()

    # ------------------------------------------------------------------ tray
    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("BoostAI")
        menu = QMenu()
        menu.addAction("Open BoostAI", self.restore_window)
        menu.addAction("Quick scan", lambda: (self.restore_window(), self.navigate("scan:quick")))
        menu.addAction("Gaming mode", lambda: (self.restore_window(), self.show_page("gaming")))
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore_window()
                                    if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.engine.monitor.set_background(False)

    def quit_app(self) -> None:
        self._really_quit = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._really_quit and self.engine.settings.minimize_to_tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.engine.monitor.set_background(True)
            if not getattr(self, "_told_tray", False):
                self._told_tray = True
                self.tray.showMessage("BoostAI is still running", "Monitoring continues quietly in the tray. "
                                      "Right-click the icon to quit.", QSystemTrayIcon.Information, 4000)
            return
        log_event("app_stopped")
        self.watch_timer.stop()
        self.tray.hide()
        event.accept()
        QApplication.instance().quit()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == event.Type.WindowStateChange:
            self.engine.monitor.set_background(self.isMinimized())
        super().changeEvent(event)

    # ----------------------------------------------------------------- watch
    def _watch(self) -> None:
        if not self.engine.settings.tray_notifications:
            return

        def done(issues):
            now = time.time()
            for issue in issues:
                if now - self._notified.get(issue.key, 0) < NOTIFY_COOLDOWN_S:
                    continue
                self._notified[issue.key] = now
                self.tray.showMessage(issue.title, issue.evidence[0] if issue.evidence else issue.explanation,
                                      QSystemTrayIcon.Warning, 8000)
                break

        run_async(self.engine.watch, on_result=done)
