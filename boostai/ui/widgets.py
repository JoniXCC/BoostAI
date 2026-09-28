"""Reusable presentation widgets."""

from __future__ import annotations

import html

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from boostai.ui import theme


def card(parent: QWidget | None = None) -> QFrame:
    f = QFrame(parent)
    f.setObjectName("Card")
    return f


def label(text: str = "", *, muted: bool = False, bold: bool = False, size: float | None = None,
          wrap: bool = False, object_name: str | None = None) -> QLabel:
    lbl = QLabel(text)
    if muted:
        lbl.setProperty("muted", True)
    if object_name:
        lbl.setObjectName(object_name)
    if bold or size:
        f = lbl.font()
        if bold:
            f.setBold(True)
        if size:
            f.setPointSizeF(size)
        lbl.setFont(f)
    lbl.setWordWrap(wrap)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


def page_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    lay.setSpacing(2)
    lay.addWidget(label(title, object_name="PageTitle"))
    if subtitle:
        lay.addWidget(label(subtitle, muted=True, wrap=True))
    return w


def badge_html(text: str, color: str) -> str:
    return (f'<span style="background-color:{color}; color:#ffffff; border-radius:6px; padding:1px 6px; '
            f'font-weight:600; font-size:8.5pt">&nbsp;{html.escape(text)}&nbsp;</span>')


def severity_badge(severity: str) -> str:
    return badge_html(severity, theme.severity_color(severity))


class StatCard(QFrame):
    """Big number + caption + optional usage bar."""

    def __init__(self, title: str, show_bar: bool = True) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumHeight(118)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)
        self.title = label(title.upper(), muted=True, size=8.5, bold=True)
        self.value = label("--", size=22, bold=True)
        self.caption = label("", muted=True, wrap=True)
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addWidget(self.caption)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setVisible(show_bar)
        lay.addWidget(self.bar)
        lay.addStretch(1)

    def set(self, value: str, caption: str = "", percent: float | None = None, color: str | None = None) -> None:
        self.value.setText(value)
        self.caption.setText(caption)
        if percent is not None:
            self.bar.setValue(int(max(0, min(100, percent))))
            c = color or theme.current().accent
            self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {c}; border-radius: 3px; }}")
        if color:
            self.value.setStyleSheet(f"color: {color};")


class ScoreRing(QWidget):
    """Circular gauge for the performance-health score."""

    def __init__(self, size: int = 150) -> None:
        super().__init__()
        self._score: float | None = None
        self._label = "Not scanned"
        self.setFixedSize(QSize(size, size))

    def set_score(self, score: float | None, label_text: str) -> None:
        self._score, self._label = score, label_text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        p = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(10, 10, self.width() - 20, self.height() - 20)
        painter.setPen(QPen(QColor(p.border), 11, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)
        if self._score is not None:
            painter.setPen(QPen(QColor(theme.score_color(self._score)), 11, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, 90 * 16, int(-360 * 16 * self._score / 100))
        painter.setPen(QColor(p.text))
        f = QFont(self.font())
        f.setPointSizeF(24)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(rect.adjusted(0, -12, 0, -12), Qt.AlignCenter, "--" if self._score is None else f"{self._score:.0f}")
        f.setPointSizeF(8.5)
        f.setBold(False)
        painter.setFont(f)
        painter.setPen(QColor(p.muted))
        painter.drawText(rect.adjusted(0, 34, 0, 34), Qt.AlignCenter, self._label)


class CategoryBar(QWidget):
    def __init__(self, name: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.name = label(name)
        self.name.setFixedWidth(120)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.value = label("--", bold=True)
        self.value.setFixedWidth(36)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.name)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.value)

    def set(self, score: float, tooltip: str = "") -> None:
        self.bar.setValue(int(score))
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {theme.score_color(score)}; border-radius: 4px; }}")
        self.value.setText(f"{score:.0f}")
        self.setToolTip(tooltip)


class Banner(QFrame):
    def __init__(self, text: str = "", warn: bool = False) -> None:
        super().__init__()
        self.setObjectName("WarnBanner" if warn else "Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        self.text = label(text, wrap=True)
        self.text.setTextFormat(Qt.RichText)
        lay.addWidget(self.text, 1)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def set_text(self, text: str) -> None:
        self.text.setText(text)


def app_icon() -> QIcon:
    """Programmatic app icon (gradient tile with a lightning bolt) - no binary asset needed at runtime."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, QColor("#4f8cff"))
        grad.setColorAt(1, QColor("#22c1a9"))
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        r = size * 0.22
        painter.drawRoundedRect(QRectF(0, 0, size, size), r, r)
        bolt = QPainterPath()
        s = size / 64.0
        pts = [(36, 8), (16, 36), (30, 36), (26, 56), (48, 26), (34, 26), (38, 8)]
        bolt.moveTo(pts[0][0] * s, pts[0][1] * s)
        for x, y in pts[1:]:
            bolt.lineTo(x * s, y * s)
        bolt.closeSubpath()
        painter.setBrush(QColor("#ffffff"))
        painter.drawPath(bolt)
        painter.end()
        icon.addPixmap(pm)
    return icon
