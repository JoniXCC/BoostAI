"""The one UI path for changing the system: validate -> approve -> apply -> verify -> show results."""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QMessageBox, QWidget

from boostai.actions.action_models import ActionRequest, Approval
from boostai.core.engine import OptimizationReport, PlannedAction
from boostai.core.issues import ActionProposal
from boostai.ui.bridge import EngineBridge
from boostai.ui.dialogs import ApprovalDialog, ProgressDialog, ResultsDialog, ReviewItem
from boostai.ui.workers import run_async


def requests_from_proposals(proposals: list[ActionProposal], source: str = "rule",
                            issue_keys: list[str | None] | None = None) -> list[tuple[ActionRequest, str]]:
    out = []
    for n, p in enumerate(proposals):
        key = issue_keys[n] if issue_keys else None
        out.append((ActionRequest(action_id=p.action_id, params=dict(p.params), source=source, issue_key=key), p.label))
    return out


class ActionFlow:
    def __init__(self, bridge: EngineBridge, parent: QWidget) -> None:
        self.bridge = bridge
        self.engine = bridge.engine
        self.parent = parent

    def run(self, requests: list[tuple[ActionRequest, str]], *, title: str = "Applying changes", measure: bool = True,
            gaming: bool = False, on_done: Callable[[OptimizationReport], None] | None = None) -> None:
        if not requests:
            return
        progress = ProgressDialog("Checking safety...", self.parent)
        progress.update_progress(10, "Validating each change against BoostAI's safety rules...")
        progress.show()

        def validate_all():
            reviewed = []
            for request, label_text in requests:
                handler = self.engine.registry.get(request.action_id)
                v = self.engine.validate(request)
                reason = ""
                if handler is not None and v.params is not None:
                    reason = handler.admin_reason(v.params)
                reviewed.append(ReviewItem(request, label_text, v, handler.definition if handler else None, reason))
            return reviewed

        def on_validated(reviewed: list[ReviewItem]) -> None:
            progress.close()
            dialog = ApprovalDialog(reviewed, self.engine.settings.offer_restore_point, self.parent)
            if dialog.exec() != ApprovalDialog.Accepted:
                return
            approved = dialog.approved_requests()
            if dialog.restore_point.isVisible() and dialog.restore_point.isChecked():
                approved.insert(0, ActionRequest(action_id="CREATE_RESTORE_POINT", params={}, source="user"))
            # The Approval token is created only here, after the explicit click.
            planned = [PlannedAction(r, Approval(r.request_id)) for r in approved]
            self._apply(planned, title, measure, gaming, on_done)

        run_async(validate_all, on_result=on_validated,
                  on_error=lambda e: (progress.close(), QMessageBox.warning(self.parent, "Validation failed", e)))

    def _apply(self, planned: list[PlannedAction], title: str, measure: bool, gaming: bool, on_done) -> None:
        progress = ProgressDialog(title, self.parent)
        progress.show()
        fn = self.engine.start_gaming_mode if gaming else self.engine.apply

        def done(report: OptimizationReport) -> None:
            progress.close()
            self.bridge.actions_changed.emit()
            ResultsDialog(report, self.parent).exec()
            if on_done:
                on_done(report)

        kwargs = {} if gaming else {"measure": measure}
        run_async(fn, planned, with_progress=True, on_progress=progress.update_progress, on_result=done,
                  on_error=lambda e: (progress.close(), QMessageBox.critical(self.parent, "Error", e)), **kwargs)
