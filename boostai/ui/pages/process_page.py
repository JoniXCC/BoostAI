"""Process explorer with protected-process awareness."""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QMenu, QMessageBox, QPushButton, QTableView

from boostai.actions.action_models import ActionRequest
from boostai.core.models import fmt_bytes
from boostai.security.protected_processes import current_username, evaluate_process
from boostai.ui import theme
from boostai.ui.action_flow import ActionFlow
from boostai.ui.pages.base import Page
from boostai.ui.widgets import label, page_header
from boostai.ui.workers import run_async

COLUMNS = ["Name", "PID", "Private memory", "Working set", "CPU %", "Threads", "Handles", "Disk/IO", "User", "Status", "Protection"]


class ProcessModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list = []
        self.verdicts: dict = {}

    def set_rows(self, rows, verdicts) -> None:
        self.beginResetModel()
        self.rows, self.verdicts = rows, verdicts
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        p = self.rows[index.row()]
        col = index.column()
        verdict = self.verdicts.get(p.key)
        if role == Qt.DisplayRole:
            return [p.name, str(p.pid), fmt_bytes(p.private), fmt_bytes(p.rss), f"{p.cpu_percent:.1f}",
                    str(p.threads), "" if p.handles is None else str(p.handles), f"{fmt_bytes(p.io_bps)}/s",
                    p.username or "(not accessible)", p.status,
                    "Protected" if verdict and verdict.protected else "User app"][col]
        if role == Qt.UserRole:  # sort key
            return [p.name.lower(), p.pid, p.private, p.rss, p.cpu_percent, p.threads, p.handles or 0, p.io_bps,
                    p.username or "", p.status, bool(verdict and verdict.protected)][col]
        if role == Qt.ToolTipRole and verdict and verdict.protected:
            return verdict.message
        if role == Qt.ForegroundRole and col == 10:
            return QColor(theme.current().muted if verdict and verdict.protected else theme.current().good)
        if role == Qt.TextAlignmentRole and col in (1, 2, 3, 4, 5, 6, 7):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class ProcessPage(Page):
    key = "processes"
    title = "Processes"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self.flow = ActionFlow(bridge, self)
        self.body.addWidget(page_header(
            "Processes", "Private memory is the memory a process has committed (the best leak signal); working set is "
                         "what currently sits in RAM. Protected processes can never be closed by BoostAI."))
        row = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name...")
        row.addWidget(self.filter, 1)
        self.count = label("", muted=True)
        row.addWidget(self.count)
        self.body.addLayout(row)
        self.model = ProcessModel()
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.UserRole)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(2, Qt.DescendingOrder)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.view.setMinimumHeight(480)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.body.addWidget(self.view, 1)
        brow = QHBoxLayout()
        self.pause = QPushButton("Pause updates")
        self.pause.setCheckable(True)
        self.trim_btn = QPushButton("Trim working set")
        self.close_btn = QPushButton("Close application")
        self.restart_btn = QPushButton("Restart application")
        self.trim_btn.clicked.connect(lambda: self._act("TRIM_PROCESS_WORKING_SET"))
        self.close_btn.clicked.connect(lambda: self._act("CLOSE_USER_PROCESS"))
        self.restart_btn.clicked.connect(lambda: self._act("RESTART_PROCESS"))
        brow.addWidget(self.pause)
        brow.addStretch(1)
        for b in (self.trim_btn, self.close_btn, self.restart_btn):
            brow.addWidget(b)
        self.body.addLayout(brow)
        self._service_pids: set[int] = set()
        self._user = current_username()
        bridge.snapshot.connect(self.on_snapshot)

    def on_show(self) -> None:
        run_async(self.engine.ops.service_pids, on_result=lambda pids: setattr(self, "_service_pids", pids))
        if self.engine.monitor.latest:
            self.on_snapshot(self.engine.monitor.latest, force=True)

    def on_snapshot(self, snap, force: bool = False) -> None:
        if (not self.isVisible() and not force) or self.pause.isChecked() or not snap.processes:
            return
        selected = self._selected()
        pid = selected.pid if selected else None
        verdicts = {p.key: evaluate_process(p, self_pid=os.getpid(), service_pids=self._service_pids, user=self._user)
                    for p in snap.processes}
        self.model.set_rows(list(snap.processes), verdicts)
        self.count.setText(f"{len(snap.processes)} processes")
        if pid is not None:
            for r in range(self.proxy.rowCount()):
                src = self.proxy.mapToSource(self.proxy.index(r, 0))
                if self.model.rows[src.row()].pid == pid:
                    self.view.selectRow(r)
                    break

    def _selected(self):
        idx = self.view.selectionModel().selectedRows() if self.view.selectionModel() else []
        if not idx:
            return None
        return self.model.rows[self.proxy.mapToSource(idx[0]).row()]

    def _menu(self, pos) -> None:
        if self._selected() is None:
            return
        menu = QMenu(self)
        menu.addAction("Trim working set", lambda: self._act("TRIM_PROCESS_WORKING_SET"))
        menu.addAction("Close application", lambda: self._act("CLOSE_USER_PROCESS"))
        menu.addAction("Restart application", lambda: self._act("RESTART_PROCESS"))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _act(self, action_id: str) -> None:
        p = self._selected()
        if p is None:
            QMessageBox.information(self, "Select a process", "Select a process in the table first.")
            return
        verdict = self.model.verdicts.get(p.key)
        if verdict and verdict.protected:
            QMessageBox.warning(self, "Action blocked for safety", verdict.message)
            return
        label_text = {"TRIM_PROCESS_WORKING_SET": "Trim working set of", "CLOSE_USER_PROCESS": "Close",
                      "RESTART_PROCESS": "Restart"}[action_id]
        req = ActionRequest(action_id=action_id, params={"pid": p.pid, "create_time": p.create_time, "name": p.name},
                            source="user")
        self.flow.run([(req, f"{label_text} {p.name}")], title=f"{label_text} {p.name}", measure=False)
