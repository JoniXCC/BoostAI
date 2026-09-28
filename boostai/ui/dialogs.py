"""Approval, progress and results dialogs."""

from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from boostai.actions.action_models import ActionRequest, ValidationResult
from boostai.actions.handlers.base import ActionDefinition
from boostai.core.engine import OptimizationReport
from boostai.core.models import fmt_bytes
from boostai.ui import theme
from boostai.ui.widgets import badge_html, label

PROCESS_ACTIONS = {"RESTART_PROCESS", "CLOSE_USER_PROCESS"}
RESULT_COLORS = {
    "SUCCESS": "good", "NO_MEANINGFUL_CHANGE": "warn", "PARTIAL": "warn", "FAILED": "bad",
    "BLOCKED": "bad", "REJECTED": "bad", "CANCELLED": "muted",
}


@dataclass
class ReviewItem:
    request: ActionRequest
    label: str
    validation: ValidationResult
    definition: ActionDefinition | None
    admin_reason: str = ""


class ApprovalDialog(QDialog):
    """Explicit, informed approval. Nothing runs unless the user presses the approve button."""

    def __init__(self, items: list[ReviewItem], offer_restore_point: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review and approve changes")
        self.resize(760, 620)
        self.items = items
        self._checks: list[tuple[ReviewItem, QCheckBox, QCheckBox | None]] = []
        p = theme.current()

        root = QVBoxLayout(self)
        root.addWidget(label("Review the changes below", object_name="PageTitle"))
        root.addWidget(label("Only the checked changes will be applied. Each one is re-validated, executed through "
                             "BoostAI's whitelist, then verified by measurement.", muted=True, wrap=True))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setSpacing(10)
        any_process = any_admin = False
        for item in items:
            frame = QFrame()
            frame.setObjectName("Card")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(14, 10, 14, 10)
            ok = item.validation.ok
            check = QCheckBox(item.label)
            check.setChecked(ok)
            check.setEnabled(ok)
            f = check.font()
            f.setBold(True)
            check.setFont(f)
            fl.addWidget(check)
            d = item.definition
            meta = []
            if d:
                risk_color = {"LOW": p.good, "MEDIUM": p.warn, "HIGH": p.bad}[d.risk.value]
                meta.append(badge_html(f"{d.risk.value} RISK", risk_color))
                meta.append(badge_html(d.reversibility.label.upper(),
                                       p.info if d.reversibility.value == "REVERSIBLE" else p.muted))
                if item.validation.requires_admin:
                    meta.append(badge_html("ADMIN (UAC PROMPT)", p.warn))
                    any_admin = any_admin or ok
                meta.append(f"<span style='color:{p.muted}'>{html.escape(d.name)} &middot; "
                            f"{html.escape(d.affected_component)}</span>")
            info = QLabel(" ".join(meta))
            info.setTextFormat(Qt.RichText)
            fl.addWidget(info)
            if d:
                fl.addWidget(label(d.description, muted=True, wrap=True))
            if not ok:
                reasons = " ".join(item.validation.reasons)
                blocked = QLabel(f"<b style='color:{p.bad}'>Not available:</b> {html.escape(reasons)}")
                blocked.setWordWrap(True)
                blocked.setTextFormat(Qt.RichText)
                fl.addWidget(blocked)
            for w in item.validation.warnings:
                wl = QLabel(f"<span style='color:{p.warn}'>&#9888;</span> {html.escape(w)}")
                wl.setWordWrap(True)
                wl.setTextFormat(Qt.RichText)
                fl.addWidget(wl)
            if item.validation.requires_admin and item.admin_reason:
                fl.addWidget(label(f"Needs administrator permission because {item.admin_reason}. Only this action "
                                   "runs elevated, in a short-lived helper.", muted=True, wrap=True))
            force = None
            if ok and item.request.action_id in PROCESS_ACTIONS:
                any_process = True
                force = QCheckBox("If it does not close normally, force it to end (unsaved work may be lost)")
                fl.addWidget(force)
            self._checks.append((item, check, force))
            lay.addWidget(frame)
        lay.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self.restore_point = QCheckBox("Create a System Restore point first (administrator permission; Windows allows "
                                       "about one per 24 hours)")
        self.restore_point.setVisible(offer_restore_point and any_admin)
        self.restore_point.setChecked(False)
        root.addWidget(self.restore_point)
        self.saved_work = QCheckBox("I have saved my work in the applications that will be closed or restarted")
        self.saved_work.setVisible(any_process)
        root.addWidget(self.saved_work)

        buttons = QDialogButtonBox()
        self.approve = QPushButton("Approve && apply")
        self.approve.setObjectName("Primary")
        cancel = QPushButton("Cancel")
        buttons.addButton(self.approve, QDialogButtonBox.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.RejectRole)
        self.approve.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        root.addWidget(buttons)
        for _i, c, _f in self._checks:
            c.toggled.connect(self._update)
        self.saved_work.toggled.connect(self._update)
        self._update()

    def _update(self) -> None:
        selected = [(i, f) for i, c, f in self._checks if c.isChecked()]
        needs_ack = any(i.request.action_id in PROCESS_ACTIONS for i, _ in selected)
        self.approve.setEnabled(bool(selected) and (not needs_ack or self.saved_work.isChecked()))
        self.approve.setText(f"Approve && apply ({len(selected)})")

    def approved_requests(self) -> list[ActionRequest]:
        out = []
        for item, check, force in self._checks:
            if not check.isChecked():
                continue
            req = item.request
            if force is not None and force.isChecked():
                req = ActionRequest(action_id=req.action_id, params={**req.params, "force_if_unresponsive": True},
                                    source=req.source, issue_key=req.issue_key)
            out.append(req)
        return out


class ProgressDialog(QDialog):
    def __init__(self, title: str, parent=None, cancellable: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        lay = QVBoxLayout(self)
        self.message = label("Starting...", wrap=True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        lay.addWidget(label(title, bold=True, size=12))
        lay.addWidget(self.message)
        lay.addWidget(self.bar)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(cancellable)
        lay.addWidget(self.cancel_button, alignment=Qt.AlignRight)

    def update_progress(self, pct: int, msg: str) -> None:
        self.bar.setValue(pct)
        self.message.setText(msg)

    def reject(self) -> None:  # Esc does nothing: work in progress must finish or be cancelled explicitly
        if self.cancel_button.isVisible():
            self.cancel_button.click()


def _fmt_value(key: str, value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)) and any(k in key for k in ("bytes", "memory", "working_set", "disk_free")):
        return fmt_bytes(value)
    return str(value)


class ResultsDialog(QDialog):
    """Before/after comparison and per-action verification. Only measured numbers are shown."""

    def __init__(self, report: OptimizationReport, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Results")
        self.resize(860, 640)
        p = theme.current()
        lay = QVBoxLayout(self)
        lay.addWidget(label("Optimization results", object_name="PageTitle"))

        text = QTextBrowser()
        parts = []
        if report.comparison and report.comparison.highlights:
            parts.append(f"<h3 style='color:{p.good}'>Measured improvements</h3><ul>" +
                         "".join(f"<li>{html.escape(h)}</li>" for h in report.comparison.highlights) + "</ul>")
        elif report.comparison:
            parts.append(f"<h3 style='color:{p.warn}'>No meaningful system-wide improvement was measured</h3>"
                         "<p>Individual actions may still have succeeded (see below). Changes such as startup items "
                         "take effect at the next sign-in.</p>")
        for note in report.notes:
            parts.append(f"<p style='color:{p.muted}'>{html.escape(note)}</p>")
        if report.comparison and report.before and report.after:
            rows = "".join(
                f"<tr><td>{html.escape(r.metric)}</td><td>{html.escape(r.before)}</td><td>{html.escape(r.after)}</td>"
                f"<td>{html.escape(r.change)}</td><td style='color:{self._verdict_color(r.verdict)}'>"
                f"{html.escape(r.verdict)}</td></tr>" for r in report.comparison.rows)
            parts.append("<h3>Before / after</h3><table cellpadding='5' width='100%'>"
                         "<tr><th align='left'>Metric</th><th align='left'>Before</th><th align='left'>After</th>"
                         f"<th align='left'>Change</th><th align='left'>Result</th></tr>{rows}</table>"
                         f"<p style='color:{p.muted}'>Both measurements used the same {int(report.before.window_seconds)}"
                         "-second window. Differences smaller than normal noise are reported as no meaningful change.</p>")
        parts.append("<h3>Actions</h3>")
        for o in report.outcomes:
            color = getattr(p, RESULT_COLORS.get(o.status.value, "muted"))
            parts.append(f"<p><b>{html.escape(o.action_id.replace('_', ' ').title())}</b> &middot; "
                         f"{html.escape(o.target)}<br><b style='color:{color}'>Result: {html.escape(o.status.label)}</b>"
                         f"<br>{html.escape(o.message)}</p>")
            v = o.verification
            if v and (v.before or v.after):
                keys = list(dict.fromkeys([*v.before.keys(), *v.after.keys()]))
                cells = "".join(f"<tr><td>{html.escape(k.replace('_', ' '))}</td><td>{html.escape(_fmt_value(k, v.before.get(k)))}"
                                f"</td><td>{html.escape(_fmt_value(k, v.after.get(k)))}</td></tr>" for k in keys)
                parts.append(f"<table cellpadding='3' style='color:{p.muted}'><tr><th align='left'></th>"
                             f"<th align='left'>Before</th><th align='left'>After</th></tr>{cells}</table>")
            if o.rollback_available:
                parts.append(f"<p style='color:{p.info}'>This change can be undone from the History page.</p>")
        text.setHtml("".join(parts))
        lay.addWidget(text, 1)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)

    @staticmethod
    def _verdict_color(verdict: str) -> str:
        p = theme.current()
        return p.good if verdict == "improved" else p.bad if verdict == "worse" else p.muted


def simple_table(headers: list[str], rows: list[list[str]], stretch_col: int = 0) -> QTableWidget:
    t = QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            t.setItem(r, c, QTableWidgetItem(val))
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    return t


def status_color(status: str) -> str:
    return getattr(theme.current(), RESULT_COLORS.get(status, "muted"))

