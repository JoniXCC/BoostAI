"""Recommended changes checklist -> approval -> apply -> before/after comparison."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QPushButton, QTreeWidget, QTreeWidgetItem

from boostai.config.settings import OptimizationMode
from boostai.security.safety_rules import risk_allowed
from boostai.ui import theme
from boostai.ui.action_flow import ActionFlow
from boostai.ui.pages.base import Page
from boostai.actions.action_models import ActionRequest
from boostai.ui.widgets import Banner, page_header


class OptimizerPage(Page):
    key = "optimizer"
    title = "Optimizer"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self.flow = ActionFlow(bridge, self)
        self.body.addWidget(page_header(
            "Recommended changes", "Tick the changes you approve. Each one is validated again, applied through the "
                                   "whitelist, verified, and the system is re-measured afterwards."))
        self.mode_banner = Banner("")
        self.body.addWidget(self.mode_banner)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Change", "Risk", "Reversible", "Admin", "From issue"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 5):
            self.tree.header().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.tree.setMinimumHeight(380)
        self.tree.itemChanged.connect(self._update_button)
        self.body.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.select_low = QPushButton("Select low-risk only")
        self.select_low.clicked.connect(lambda: self._select(lambda risk: risk == "LOW"))
        self.select_none = QPushButton("Clear selection")
        self.select_none.clicked.connect(lambda: self._select(lambda _r: False))
        self.apply_btn = QPushButton("Apply selected")
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.clicked.connect(self.apply)
        row.addWidget(self.select_low)
        row.addWidget(self.select_none)
        row.addStretch(1)
        row.addWidget(self.apply_btn)
        self.body.addLayout(row)
        self.body.addWidget(Banner("Default behaviour: BoostAI never changes your system automatically. Nothing on "
                                   "this page runs until you approve it in the next step."))
        self._items: list[tuple[QTreeWidgetItem, object, object]] = []
        bridge.scan_finished.connect(self.load)
        bridge.settings_changed.connect(self._mode_text)
        self._mode_text()
        self._update_button()

    def _mode_text(self) -> None:
        mode = self.engine.settings.mode
        if mode == OptimizationMode.SAFE:
            self.mode_banner.set_text("<b>Safe mode:</b> only low-risk changes can be applied. Medium-risk items are "
                                      "shown as recommendations only. Change the mode in Settings.")
        else:
            self.mode_banner.set_text("<b>Balanced mode:</b> low- and medium-risk changes can be applied after your "
                                      "approval. High-risk operations are never automated.")
        if self.engine.last_scan:
            self.load(self.engine.last_scan)

    def load(self, result) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items.clear()
        mode = self.engine.settings.mode
        p = theme.current()
        seen: set[tuple] = set()
        ctx = self.engine.action_context()
        for issue in result.issues:
            for prop in issue.proposals:
                key = (prop.action_id, tuple(sorted((k, str(v)) for k, v in prop.params.items())))
                if key in seen:
                    continue
                seen.add(key)
                d = self.engine.registry.definition(prop.action_id)
                if d is None:
                    continue
                allowed = risk_allowed(d.risk.value, mode)
                admin = "Yes (UAC prompt)" if self._needs_admin(prop, ctx) else "No"
                item = QTreeWidgetItem([prop.label, d.risk.value.title(), d.reversibility.label, admin, issue.title])
                item.setToolTip(0, prop.rationale + (f"\n⚠ {prop.warning}" if prop.warning else ""))
                item.setForeground(1, QColor({"LOW": p.good, "MEDIUM": p.warn, "HIGH": p.bad}[d.risk.value]))
                if allowed:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Unchecked)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                    item.setToolTip(0, f"Recommendation only in {mode.value.upper()} mode.")
                self.tree.addTopLevelItem(item)
                self._items.append((item, prop, issue))
        self.tree.blockSignals(False)
        self._update_button()

    def _needs_admin(self, prop, ctx) -> bool:
        handler = self.engine.registry.get(prop.action_id)
        try:
            return handler.requires_admin(handler.params_model.model_validate(prop.params), ctx) and not ctx.is_admin
        except Exception:
            return True

    def _select(self, predicate) -> None:
        for item, prop, _issue in self._items:
            if item.flags() & Qt.ItemIsUserCheckable:
                risk = self.engine.registry.definition(prop.action_id).risk.value
                item.setCheckState(0, Qt.Checked if predicate(risk) else Qt.Unchecked)

    def _selected(self):
        return [(prop, issue) for item, prop, issue in self._items
                if item.flags() & Qt.ItemIsUserCheckable and item.checkState(0) == Qt.Checked]

    def _update_button(self, *_a) -> None:
        n = len(self._selected())
        self.apply_btn.setEnabled(n > 0)
        self.apply_btn.setText(f"Apply selected ({n})" if n else "Apply selected")

    def apply(self) -> None:
        reqs = [(ActionRequest(action_id=p.action_id, params=dict(p.params), source="rule", issue_key=i.key), p.label)
                for p, i in self._selected()]
        self.flow.run(reqs, title="Applying approved changes",
                      on_done=lambda _r: self.bridge.navigate.emit("scan:quick-silent"))
