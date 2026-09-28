"""Startup programs and Windows services."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from boostai.actions.action_models import ActionRequest
from boostai.knowledge.startup_catalog import StartupCategory, assess
from boostai.metrics import services, startup
from boostai.security.protected_services import ServiceClass, classify_service
from boostai.ui import theme
from boostai.ui.action_flow import ActionFlow
from boostai.ui.pages.base import Page
from boostai.ui.widgets import label, page_header
from boostai.ui.workers import run_async

CATEGORY_TEXT = {
    StartupCategory.PROTECTED: "Protected", StartupCategory.RECOMMENDED_KEEP: "Recommended: keep",
    StartupCategory.OPTIONAL: "Optional", StartupCategory.HIGH_IMPACT: "High impact", StartupCategory.UNKNOWN: "Unknown",
}


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.setMinimumHeight(460)
    t.setSortingEnabled(True)
    t.setTextElideMode(Qt.ElideMiddle)
    t.setWordWrap(False)
    return t


class StartupPage(Page):
    key = "startup"
    title = "Startup & services"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self.flow = ActionFlow(bridge, self)
        self.body.addWidget(page_header(
            "Startup programs & services", "Disabling a startup item uses the same reversible switch as Task Manager; "
            "nothing is deleted or uninstalled. Services can only be stopped if they are on BoostAI's optional list."))
        tabs = QTabWidget()
        self.body.addWidget(tabs, 1)

        # Startup tab
        st = QWidget()
        sl = QVBoxLayout(st)
        self.startup_table = _table(["Name", "Status", "Category", "Estimated impact", "Publisher", "Location", "Command"])
        self.startup_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        sl.addWidget(self.startup_table, 1)
        row = QHBoxLayout()
        self.startup_note = label("", muted=True, wrap=True)
        row.addWidget(self.startup_note, 1)
        self.keep_btn = QPushButton("Always keep (don't recommend)")
        self.keep_btn.clicked.connect(self.keep)
        self.enable_btn = QPushButton("Enable at startup")
        self.enable_btn.clicked.connect(lambda: self.toggle(True))
        self.disable_btn = QPushButton("Disable at startup")
        self.disable_btn.setObjectName("Primary")
        self.disable_btn.clicked.connect(lambda: self.toggle(False))
        for b in (self.keep_btn, self.enable_btn, self.disable_btn):
            row.addWidget(b)
        sl.addLayout(row)
        tabs.addTab(st, "Startup programs")

        # Services tab
        sv = QWidget()
        vl = QVBoxLayout(sv)
        self.service_table = _table(["Display name", "Name", "Status", "Start type", "BoostAI classification", "Notes"])
        self.service_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        vl.addWidget(self.service_table, 1)
        srow = QHBoxLayout()
        srow.addWidget(label("Only services on the optional allow-list can be changed. Start types are never "
                             "modified; stopped services return after a restart.", muted=True, wrap=True), 1)
        self.stop_btn = QPushButton("Stop")
        self.start_btn = QPushButton("Start")
        self.restart_btn = QPushButton("Restart")
        self.stop_btn.clicked.connect(lambda: self.service_action("STOP_OPTIONAL_SERVICE"))
        self.start_btn.clicked.connect(lambda: self.service_action("RESTORE_OPTIONAL_SERVICE"))
        self.restart_btn.clicked.connect(lambda: self.service_action("RESTART_SERVICE"))
        for b in (self.stop_btn, self.start_btn, self.restart_btn):
            srow.addWidget(b)
        vl.addLayout(srow)
        tabs.addTab(sv, "Services")
        self._startup_items = []
        self._services = []
        bridge.actions_changed.connect(self.on_show)

    def on_show(self) -> None:
        def load():
            procs = self.engine.monitor.latest.processes if self.engine.monitor.latest else []
            items = startup.collect_startup_items()
            return items, [assess(i, procs) for i in items], services.collect_services()

        run_async(load, on_result=self._fill)

    def _fill(self, data) -> None:
        items, assessments, svc = data
        p = theme.current()
        keep = set(self.engine.settings.startup_keep_ids)
        self._startup_items = list(zip(items, assessments))
        t = self.startup_table
        t.setSortingEnabled(False)
        t.setRowCount(len(items))
        for r, (item, a) in enumerate(self._startup_items):
            cat = CATEGORY_TEXT[a.category] + (" (kept by you)" if item.item_id in keep else "")
            values = [item.display_name, "Enabled" if item.enabled else "Disabled", cat,
                      f"{a.impact} ({a.impact_basis})", item.publisher or "-", item.source.label, item.command]
            for c, v in enumerate(values):
                cell = QTableWidgetItem(v)
                cell.setData(Qt.UserRole, r)
                if c == 1:
                    cell.setForeground(QColor(p.good if item.enabled else p.muted))
                if c == 2:
                    cell.setToolTip(a.reason)
                if c == 6:
                    cell.setToolTip(item.command)
                t.setItem(r, c, cell)
        t.setSortingEnabled(True)
        enabled = sum(1 for i in items if i.enabled)
        self.startup_note.setText(f"{enabled} of {len(items)} startup items are enabled. Impact is an estimate: "
                                  "catalog knowledge or the program's measured memory while running.")

        self._services = sorted(svc, key=lambda s: (s.status != "running", s.display_name.lower()))
        st = self.service_table
        st.setSortingEnabled(False)
        st.setRowCount(len(self._services))
        for r, s in enumerate(self._services):
            cls, why = classify_service(s.name)
            values = [s.display_name, s.name, s.status, s.start_type, cls.value.title(), why]
            for c, v in enumerate(values):
                cell = QTableWidgetItem(v)
                cell.setData(Qt.UserRole, r)
                if c == 4:
                    cell.setForeground(QColor({ServiceClass.PROTECTED: p.muted, ServiceClass.OPTIONAL: p.good,
                                               ServiceClass.UNKNOWN: p.warn}[cls]))
                st.setItem(r, c, cell)
        st.setSortingEnabled(True)

    def _selected_startup(self):
        rows = self.startup_table.selectionModel().selectedRows()
        if not rows:
            return None
        r = self.startup_table.item(rows[0].row(), 0).data(Qt.UserRole)
        return self._startup_items[r]

    def toggle(self, enable: bool) -> None:
        sel = self._selected_startup()
        if not sel:
            return
        item, _a = sel
        action = "ENABLE_STARTUP_ITEM" if enable else "DISABLE_STARTUP_ITEM"
        req = ActionRequest(action_id=action, params={"item_id": item.item_id}, source="user")
        self.flow.run([(req, f"{'Enable' if enable else 'Disable'} {item.display_name} at startup")],
                      title="Updating startup programs", measure=False)

    def keep(self) -> None:
        sel = self._selected_startup()
        if not sel:
            return
        s = self.engine.settings.model_copy(deep=True)
        if sel[0].item_id not in s.startup_keep_ids:
            s.startup_keep_ids.append(sel[0].item_id)
        self.engine.save_settings(s)
        self.bridge.settings_changed.emit()
        self.on_show()

    def service_action(self, action_id: str) -> None:
        rows = self.service_table.selectionModel().selectedRows()
        if not rows:
            return
        svc = self._services[self.service_table.item(rows[0].row(), 0).data(Qt.UserRole)]
        verb = {"STOP_OPTIONAL_SERVICE": "Stop", "RESTORE_OPTIONAL_SERVICE": "Start", "RESTART_SERVICE": "Restart"}[action_id]
        req = ActionRequest(action_id=action_id, params={"name": svc.name}, source="user")
        self.flow.run([(req, f"{verb} service {svc.display_name}")], title=f"{verb} service", measure=False)
