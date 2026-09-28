"""Issue list with full evidence, explanation, risk and optional AI explanations."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from boostai.ui import theme
from boostai.ui.action_flow import ActionFlow, requests_from_proposals
from boostai.ui.pages.base import Page
from boostai.ui.widgets import Banner, badge_html, card, label, page_header, severity_badge
from boostai.ui.workers import run_async


def issue_html(issue, ai_text: str | None = None, ai_source: str | None = None) -> str:
    p = theme.current()
    e = html.escape

    def row(k: str, v: str) -> str:
        return f"<tr><td style='color:{p.muted}; padding-right:14px' valign='top'>{k}</td><td>{v}</td></tr>"

    evidence = "".join(f"<li><pre style='font-family:Segoe UI; white-space:pre-wrap; margin:0'>{e(x)}</pre></li>"
                       for x in issue.evidence)
    actions = "".join(f"<li><b>{e(pr.label)}</b> <span style='color:{p.muted}'>({e(pr.action_id)})</span><br>"
                      f"{e(pr.rationale)}" + (f"<br><span style='color:{p.warn}'>&#9888; {e(pr.warning)}</span>"
                                              if pr.warning else "") + "</li>" for pr in issue.proposals)
    manual = "".join(f"<li>{e(m)}</li>" for m in issue.manual_steps)
    parts = [
        f"<h2>{e(issue.title)}</h2>",
        f"<p>{severity_badge(issue.severity.value)} {badge_html('CONFIDENCE ' + issue.confidence.value, p.info)}"
        f" {badge_html(issue.root_cause.value.upper(), p.muted)}</p>",
        "<table cellpadding='3'>",
        row("Issue", e(issue.type.value.replace('_', ' ').title())),
        row("Severity", e(issue.severity.value.title())),
        row("Confidence", e(issue.confidence.value.title())),
        row("Cause category", e(issue.root_cause.value)),
        row("Affected component", e(issue.component)),
        row("Risk of recommended fix", e(issue.risk)),
        row("Expected effect", e(issue.expected_effect or "-")),
        row("Reversible?", e(issue.reversible)),
        row("Files affected", e(issue.files_affected)),
        "</table>",
        f"<h3>Evidence</h3><ul>{evidence}</ul>",
        f"<h3>Explanation</h3><p>{e(issue.explanation)}</p>",
    ]
    if ai_text:
        parts.append(f"<h3>Plain-language explanation <span style='color:{p.muted}; font-size:9pt'>"
                     f"({e(ai_source or '')})</span></h3><p>{e(ai_text)}</p>")
    parts.append(f"<h3>Recommended action</h3><ul>{actions}</ul>" if actions else
                 "<h3>Recommended action</h3><p>No automatic fix is appropriate for this issue.</p>")
    if manual:
        parts.append(f"<h3>What you can do manually</h3><ul>{manual}</ul>")
    return "".join(parts)


class IssuesPage(Page):
    key = "issues"
    title = "Issues"

    def __init__(self, bridge) -> None:
        super().__init__(bridge)
        self.flow = ActionFlow(bridge, self)
        self.result = None
        self._ai_cache: dict[str, tuple[str, str]] = {}
        self.body.addWidget(page_header("Issues", "Every finding shows its evidence, how confident BoostAI is, and "
                                        "whether it can be fixed safely or needs manual action."))
        self.ai_banner = Banner("")
        self.body.addWidget(self.ai_banner)
        arow = QHBoxLayout()
        self.summary_btn = QPushButton("Get AI summary and priorities")
        self.summary_btn.clicked.connect(self.ai_summary)
        arow.addWidget(self.summary_btn)
        arow.addStretch(1)
        self.body.addLayout(arow)
        self.summary_card = card()
        sl = QVBoxLayout(self.summary_card)
        sl.setContentsMargins(16, 12, 16, 12)
        self.summary_text = QTextBrowser()
        self.summary_text.setMinimumHeight(150)
        sl.addWidget(self.summary_text)
        self.summary_card.setVisible(False)
        self.body.addWidget(self.summary_card)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(300)
        self.list.currentRowChanged.connect(self.show_issue)
        split.addWidget(self.list)
        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        dl.addWidget(self.detail, 1)
        brow = QHBoxLayout()
        self.explain_btn = QPushButton("Explain in plain language (AI)")
        self.explain_btn.clicked.connect(self.explain)
        self.fix_btn = QPushButton("Review this fix")
        self.fix_btn.setObjectName("Primary")
        self.fix_btn.clicked.connect(self.fix)
        brow.addWidget(self.explain_btn)
        brow.addStretch(1)
        brow.addWidget(self.fix_btn)
        dl.addLayout(brow)
        split.addWidget(detail)
        split.setSizes([340, 700])
        split.setMinimumHeight(520)
        self.body.addWidget(split, 1)
        self.detail.setHtml("<p>Run a scan to see issues.</p>")
        self.fix_btn.setEnabled(False)
        self.explain_btn.setEnabled(False)
        bridge.scan_finished.connect(self.load)
        bridge.settings_changed.connect(self.update_ai_banner)
        self.update_ai_banner()

    def update_ai_banner(self) -> None:
        s = self.engine.settings.ai
        p = theme.current()
        if s.provider.value == "none":
            text = "AI is off. All findings and recommendations come from BoostAI's deterministic rules."
        elif s.provider.is_cloud and not s.local_only:
            text = (f"<b style='color:{p.warn}'>Cloud AI enabled.</b> System performance metadata (numbers, issue "
                    "types and process names - never file paths, documents or personal data) may be sent to the "
                    f"configured AI provider ({s.provider.value.title()}) when you press an AI button.")
        elif s.provider.is_cloud:
            text = "Local-only mode is on, so the configured cloud AI is blocked. No data leaves this computer."
        else:
            text = "Local AI (Ollama) is enabled. Requests stay on this computer."
        self.ai_banner.set_text(text)

    def load(self, result) -> None:
        self.result = result
        self._ai_cache.clear()
        self.summary_card.setVisible(False)
        self.list.clear()
        for issue in result.issues:
            item = QListWidgetItem(f"{issue.severity.value:<8} {issue.title}")
            item.setForeground(QColor(theme.severity_color(issue.severity.value)))
            item.setToolTip(f"{issue.root_cause.value} · confidence {issue.confidence.value.lower()}")
            self.list.addItem(item)
        if result.issues:
            self.list.setCurrentRow(0)
        else:
            self.detail.setHtml("<h2>No problems detected</h2><p>The scan did not find anything that needs attention.</p>")
            self.fix_btn.setEnabled(False)
            self.explain_btn.setEnabled(False)

    def current_issue(self):
        row = self.list.currentRow()
        if self.result is None or row < 0 or row >= len(self.result.issues):
            return None
        return self.result.issues[row]

    def show_issue(self, _row: int) -> None:
        issue = self.current_issue()
        if issue is None:
            return
        ai = self._ai_cache.get(issue.key)
        self.detail.setHtml(issue_html(issue, *(ai or (None, None))))
        self.fix_btn.setEnabled(bool(issue.proposals))
        self.explain_btn.setEnabled(self.engine.settings.ai.provider.value != "none")

    def explain(self) -> None:
        issue = self.current_issue()
        if issue is None:
            return
        self.explain_btn.setEnabled(False)
        self.explain_btn.setText("Asking AI...")

        def done(res):
            text, source = res
            self._ai_cache[issue.key] = (text, "AI: " + source if source != "rules" else "rule-based (AI unavailable)")
            self.explain_btn.setText("Explain in plain language (AI)")
            self.show_issue(self.list.currentRow())

        run_async(self.engine.advisor.explain_issue, issue, on_result=done,
                  on_error=lambda e: self.explain_btn.setText("Explain in plain language (AI)"))

    def ai_summary(self) -> None:
        if self.result is None:
            return
        self.summary_btn.setEnabled(False)
        self.summary_btn.setText("Preparing summary...")

        def done(advice):
            p = theme.current()
            e = html.escape
            source = f"AI ({advice.provider})" if advice.source == "ai" else "BoostAI rules"
            items = "".join(
                f"<li><b>{e(i.issue.title)}</b>" + (f" &rarr; <i>{e(i.proposal.label)}</i>" if i.proposal else
                                                   " &rarr; <i>manual action</i>")
                + f"<br>{e(i.explanation)}" + (f"<br><span style='color:{p.warn}'>&#9888; {e(i.warning)}</span>"
                                               if i.warning else "") + "</li>" for i in advice.items)
            note = f"<p style='color:{p.muted}'>{e(advice.note)}</p>" if advice.note else ""
            rejected = (f"<p style='color:{p.muted}'>Discarded AI suggestions that failed validation: "
                        f"{e('; '.join(advice.rejected))}</p>") if advice.rejected else ""
            self.summary_text.setHtml(f"<h3>Summary <span style='color:{p.muted}; font-size:9pt'>(source: {source}, "
                                      f"priority {e(advice.priority)})</span></h3><p>{e(advice.summary)}</p>"
                                      f"<ol>{items}</ol>{note}{rejected}")
            self.summary_card.setVisible(True)
            self.summary_btn.setEnabled(True)
            self.summary_btn.setText("Get AI summary and priorities")

        run_async(self.engine.advisor.advise, self.result.issues, self.result.snapshot, on_result=done,
                  on_error=lambda e: (self.summary_btn.setEnabled(True),
                                      self.summary_btn.setText("Get AI summary and priorities")))

    def fix(self) -> None:
        issue = self.current_issue()
        if issue and issue.proposals:
            self.flow.run(requests_from_proposals(issue.proposals, issue_keys=[issue.key] * len(issue.proposals)),
                          title=f"Fixing: {issue.title}")
