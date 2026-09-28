"""Safe temp/cache cleanup: analyse first, show exactly what would be removed, then ask."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QPushButton, QTreeWidget, QTreeWidgetItem

from boostai.actions.action_models import ActionRequest
from boostai.actions.cleanup_catalog import CATALOG, TEMP_TARGETS, estimate
from boostai.core.models import fmt_bytes
from boostai.ui import theme
from boostai.ui.action_flow import ActionFlow
from boostai.ui.pages.base import Page
from boostai.ui.widgets import Banner, label, page_header
from boostai.ui.workers import run_async
from boostai.utils import winapi


class CleanupPage(Page):
    key = "cleanup"
    title = "Cleanup"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self.flow = ActionFlow(bridge, self)
        self.body.addWidget(page_header(
            "Safe cleanup", "Only the locations listed here can ever be cleaned. Files newer than the age limit, files "
                            "in use, personal folders and browser data other than cache are never touched."))
        self.body.addWidget(Banner("Cleaning frees disk space; it does not make the PC faster by itself. Browser "
                                   "cleanup removes cache only - never cookies, history, passwords or open sessions."))
        row = QHBoxLayout()
        self.analyze_btn = QPushButton("Analyse (nothing is deleted)")
        self.analyze_btn.clicked.connect(self.analyze)
        self.status = label("", muted=True)
        row.addWidget(self.analyze_btn)
        row.addWidget(self.status, 1)
        self.body.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Location", "Files", "Size", "Notes"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.setMinimumHeight(360)
        self.tree.itemChanged.connect(self._update_total)
        self.body.addWidget(self.tree, 1)
        brow = QHBoxLayout()
        self.total = label("", bold=True)
        brow.addWidget(self.total, 1)
        self.clean_btn = QPushButton("Remove selected")
        self.clean_btn.setObjectName("Primary")
        self.clean_btn.setEnabled(False)
        self.clean_btn.clicked.connect(self.clean)
        brow.addWidget(self.clean_btn)
        self.body.addLayout(brow)
        self._estimates = []

    def on_show(self) -> None:
        if not self._estimates:
            self.analyze()

    def analyze(self) -> None:
        self.analyze_btn.setEnabled(False)
        self.status.setText("Measuring approved locations...")
        running = {p.name for p in (self.engine.monitor.latest.processes if self.engine.monitor.latest else [])}
        run_async(estimate, list(CATALOG), self.engine.settings.cleanup_min_age_hours, running, winapi.is_admin(),
                  on_result=self._fill, on_error=lambda e: self.status.setText(f"Analysis failed: {e}"),
                  on_finished=lambda: self.analyze_btn.setEnabled(True))

    def _fill(self, estimates) -> None:
        p = theme.current()
        self._estimates = estimates
        self.tree.blockSignals(True)
        self.tree.clear()
        for e in estimates:
            target = CATALOG[e.target_id]
            notes = [target.description]
            if target.note:
                notes.append(target.note)
            if e.skipped_reason:
                notes.append(f"Not available: {e.skipped_reason}")
            if target.requires_admin:
                notes.append("Requires administrator permission.")
            item = QTreeWidgetItem([target.label, str(e.files) + ("+" if e.truncated else ""), fmt_bytes(e.bytes),
                                    " ".join(notes)])
            item.setData(0, Qt.UserRole, e.target_id)
            usable = e.files > 0 and not (e.skipped_reason and "administrator" not in e.skipped_reason)
            if target.requires_admin and not winapi.is_admin():
                usable = True  # size unknown until elevated; the elevated helper measures and cleans
            if usable:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked if target.default_selected and e.files else Qt.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            if e.skipped_reason:
                item.setForeground(3, QColor(p.warn))
            item.setToolTip(3, "\n".join(e.roots) or "Location not present")
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self.status.setText(f"Files older than {self.engine.settings.cleanup_min_age_hours:g} hours are counted.")
        self._update_total()

    def _selected_ids(self) -> list[str]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.flags() & Qt.ItemIsUserCheckable and item.checkState(0) == Qt.Checked:
                out.append(item.data(0, Qt.UserRole))
        return out

    def _update_total(self, *_a) -> None:
        ids = set(self._selected_ids())
        total = sum(e.bytes for e in self._estimates if e.target_id in ids)
        self.total.setText(f"Selected: {fmt_bytes(total)}" if ids else "Nothing selected")
        self.clean_btn.setEnabled(bool(ids))

    def clean(self) -> None:
        ids = self._selected_ids()
        temp = [t for t in ids if t in TEMP_TARGETS]
        cache = [t for t in ids if t not in TEMP_TARGETS]
        by_id = {e.target_id: e for e in self._estimates}
        reqs = []
        if temp:
            size = sum(by_id[t].bytes for t in temp)
            reqs.append((ActionRequest(action_id="CLEAR_SAFE_TEMP_FILES", params={"target_ids": temp}, source="user"),
                         f"Remove {fmt_bytes(size)} of temporary files ({', '.join(CATALOG[t].label for t in temp)})"))
        for t in cache:
            reqs.append((ActionRequest(action_id="CLEAR_SPECIFIC_SAFE_CACHE", params={"target_ids": [t]}, source="user"),
                         f"Clear {CATALOG[t].label} ({fmt_bytes(by_id[t].bytes)})"))
        self.flow.run(reqs, title="Cleaning up", measure=False, on_done=lambda _r: self.analyze())
