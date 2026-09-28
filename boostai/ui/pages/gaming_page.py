"""Gaming Mode: pre-game resource check, optional closing of heavy apps, performance power plan, restore."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from boostai.actions.action_models import ActionRequest
from boostai.core.models import fmt_bytes
from boostai.ui import theme
from boostai.ui.action_flow import ActionFlow
from boostai.ui.pages.base import Page
from boostai.ui.widgets import Banner, card, label, page_header
from boostai.ui.workers import run_async


class GamingPage(Page):
    key = "gaming"
    title = "Gaming mode"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self.flow = ActionFlow(bridge, self)
        self.body.addWidget(page_header(
            "Gaming mode", "Checks free resources before you play, lets you choose heavy background apps to close and "
                           "can switch to a faster power plan. Nothing is closed without your approval; reversible "
                           "changes are restored when you end Gaming mode."))
        self.session_banner = Banner("")
        self.body.addWidget(self.session_banner)
        row = QHBoxLayout()
        self.check_btn = QPushButton("Check readiness")
        self.check_btn.setObjectName("Primary")
        self.check_btn.clicked.connect(self.check)
        self.end_btn = QPushButton("End gaming mode and restore settings")
        self.end_btn.clicked.connect(self.end)
        row.addWidget(self.check_btn)
        row.addWidget(self.end_btn)
        row.addStretch(1)
        self.body.addLayout(row)

        self.result_card = card()
        rl = QVBoxLayout(self.result_card)
        rl.setContentsMargins(16, 14, 16, 14)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setWordWrap(True)
        rl.addWidget(self.summary)
        rl.addWidget(label("Heavy applications you may close (tick to include):", object_name="SectionTitle"))
        self.apps_box = QVBoxLayout()
        rl.addLayout(self.apps_box)
        self.plan_check = QCheckBox()
        rl.addWidget(self.plan_check)
        self.start_btn = QPushButton("Start gaming mode with selected changes")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self.start)
        rl.addWidget(self.start_btn, alignment=Qt.AlignRight)
        self.result_card.setVisible(False)
        self.body.addWidget(self.result_card)
        self.body.addStretch(1)
        self._check = None
        self._app_checks: list[tuple[QCheckBox, object]] = []
        self._update_session()

    def _update_session(self) -> None:
        session = self.engine.settings.active_gaming_session
        self.end_btn.setEnabled(bool(session))
        self.session_banner.set_text("<b>Gaming mode is active.</b> End it to restore reversible settings (such as "
                                     "the power plan). Closed applications are not reopened automatically."
                                     if session else "Gaming mode is not active.")

    def check(self) -> None:
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Checking...")
        run_async(self.engine.gaming_check, on_result=self._show,
                  on_error=lambda e: QMessageBox.warning(self, "Check failed", e),
                  on_finished=lambda: (self.check_btn.setEnabled(True), self.check_btn.setText("Check readiness")))

    def _show(self, check) -> None:
        self._check = check
        p = theme.current()
        e = html.escape
        notes = "".join(f"<li>{e(n)}</li>" for n in check.notes)
        temps = ", ".join(check.temperatures) or "Temperature monitoring unavailable on this system."
        self.summary.setText(
            f"<h3>Resources right now</h3><p>Free RAM: <b>{fmt_bytes(check.available_ram)}</b> of "
            f"{fmt_bytes(check.total_ram)} ({100 - check.ram_percent:.0f}% free) · CPU load: "
            f"<b>{check.cpu_percent:.0f}%</b><br>Power plan: {e(check.active_plan.name if check.active_plan else 'unknown')}"
            f"<br><span style='color:{p.muted}'>{e(temps)}</span></p>" + (f"<ul>{notes}</ul>" if notes else ""))
        while self.apps_box.count():
            w = self.apps_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._app_checks.clear()
        if not check.heavy_apps:
            self.apps_box.addWidget(label("No heavy background applications found.", muted=True))
        for app in check.heavy_apps[:12]:
            cb = QCheckBox(f"{app.group.name} – {fmt_bytes(app.group.rss)} RAM"
                           f"{f', {app.group.count} processes' if app.group.count > 1 else ''} ({app.reason})")
            cb.setEnabled(not app.protected)
            if app.protected:
                cb.setToolTip("Action blocked for safety.")
            self.apps_box.addWidget(cb)
            self._app_checks.append((cb, app))
        if check.recommended_plan:
            self.plan_check.setText(f"Switch power plan to '{check.recommended_plan.name}' (restored when gaming mode ends)")
            self.plan_check.setVisible(True)
            self.plan_check.setChecked(True)
        else:
            self.plan_check.setVisible(False)
            self.plan_check.setChecked(False)
        self.result_card.setVisible(True)

    def start(self) -> None:
        if self._check is None:
            return
        reqs = []
        if self.plan_check.isVisible() and self.plan_check.isChecked() and self._check.recommended_plan:
            plan = self._check.recommended_plan
            reqs.append((ActionRequest(action_id="CHANGE_POWER_PLAN", params={"guid": plan.guid}, source="user"),
                         f"Switch power plan to '{plan.name}'"))
        for cb, app in self._app_checks:
            if cb.isChecked():
                r = app.root
                reqs.append((ActionRequest(action_id="CLOSE_USER_PROCESS",
                                           params={"pid": r.pid, "create_time": r.create_time, "name": r.name},
                                           source="user"), f"Close {app.group.name} ({fmt_bytes(app.group.rss)})"))
        if not reqs:
            QMessageBox.information(self, "Gaming mode", "Nothing selected. Your PC looks ready - have fun!")
            return
        self.flow.run(reqs, title="Starting gaming mode", gaming=True, on_done=lambda _r: self._update_session())

    def end(self) -> None:
        def done(results):
            self._update_session()
            self.bridge.actions_changed.emit()
            msgs = [r.message for _id, r in results] or ["Nothing needed restoring."]
            QMessageBox.information(self, "Gaming mode ended", "\n".join(msgs))

        run_async(self.engine.end_gaming_mode, True, on_result=done,
                  on_error=lambda e: QMessageBox.warning(self, "Error", e))

    def on_show(self) -> None:
        self._update_session()
