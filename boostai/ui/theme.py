"""Dark / light themes (Qt style sheets built from a small token palette)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    good: str
    warn: str
    bad: str
    critical: str
    info: str
    chart_grid: str


DARK = Palette("dark", "#0e1116", "#161a22", "#1d222c", "#2a303c", "#e7eaf0", "#98a2b3", "#4f8cff", "#ffffff",
               "#3ecf8e", "#f5c542", "#ff8a3d", "#ff4d6d", "#5fb3ff", "#2a303c")
LIGHT = Palette("light", "#f4f6fa", "#ffffff", "#eef1f6", "#d7dce5", "#1b2230", "#5b6576", "#2f6fe4", "#ffffff",
                "#1f9d6a", "#b7860b", "#d9651c", "#d7263d", "#1f7ae0", "#dde2ea")

_current: Palette = DARK

SEVERITY_COLORS = {"CRITICAL": "critical", "HIGH": "bad", "MEDIUM": "warn", "LOW": "info", "INFO": "muted"}


def current() -> Palette:
    return _current


def severity_color(severity: str) -> str:
    return getattr(_current, SEVERITY_COLORS.get(severity, "muted"))


def score_color(score: float) -> str:
    p = _current
    return p.good if score >= 85 else p.info if score >= 70 else p.warn if score >= 50 else p.critical


def stylesheet(p: Palette) -> str:
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt; color: {p.text}; }}
QMainWindow, QDialog, #Page {{ background: {p.bg}; }}
QWidget#Sidebar {{ background: {p.surface}; border-right: 1px solid {p.border}; }}
QLabel#AppTitle {{ font-size: 15pt; font-weight: 700; }}
QLabel#AppSubtitle, QLabel.muted, QLabel[muted="true"] {{ color: {p.muted}; }}
QLabel#PageTitle {{ font-size: 17pt; font-weight: 650; }}
QLabel#SectionTitle {{ font-size: 11.5pt; font-weight: 650; }}
QPushButton#NavButton {{
    text-align: left; padding: 9px 14px; border: none; border-radius: 8px; background: transparent;
    color: {p.muted}; font-size: 10.5pt;
}}
QPushButton#NavButton:hover {{ background: {p.surface2}; color: {p.text}; }}
QPushButton#NavButton:checked {{ background: {p.surface2}; color: {p.text}; font-weight: 600;
    border-left: 3px solid {p.accent}; }}
QFrame#Card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
QFrame#Banner {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 10px; }}
QFrame#WarnBanner {{ background: {p.surface2}; border: 1px solid {p.warn}; border-radius: 10px; }}
QPushButton {{
    background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {p.accent}; }}
QPushButton:disabled {{ color: {p.muted}; border-color: {p.border}; }}
QPushButton#Primary {{ background: {p.accent}; color: {p.accent_text}; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {QColor(p.accent).lighter(115).name()}; }}
QPushButton#Primary:disabled {{ background: {p.border}; color: {p.muted}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid {p.bad}; color: {p.bad}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {p.surface2}; border: 1px solid {p.border}; border-radius: 7px; padding: 5px 8px;
    selection-background-color: {p.accent};
}}
QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border}; selection-background-color: {p.accent}; }}
QTextBrowser {{ background: transparent; border: none; }}
QTableView, QTreeWidget, QListWidget, QTableWidget {{
    background: {p.surface}; alternate-background-color: {p.surface2}; border: 1px solid {p.border};
    border-radius: 10px; gridline-color: {p.border}; selection-background-color: {QColor(p.accent).darker(140).name() if p.name == 'dark' else QColor(p.accent).lighter(170).name()};
    selection-color: {p.text};
}}
QHeaderView::section {{ background: {p.surface2}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border};
    padding: 6px 8px; font-weight: 600; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; color: {p.muted}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
QProgressBar {{ background: {p.surface2}; border: none; border-radius: 6px; height: 12px; text-align: center; color: {p.text}; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 6px; }}
QCheckBox::indicator, QTreeWidget::indicator, QTableWidget::indicator {{ width: 16px; height: 16px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; min-width: 30px; }}
QToolTip {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; padding: 6px; }}
QStatusBar {{ background: {p.surface}; color: {p.muted}; border-top: 1px solid {p.border}; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 10px; margin-top: 14px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p.muted}; font-weight: 600; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.surface2}; }}
"""


def apply_theme(app: QApplication, name: str) -> Palette:
    global _current
    _current = LIGHT if name == "light" else DARK
    p = _current
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(p.bg))
    pal.setColor(QPalette.Base, QColor(p.surface))
    pal.setColor(QPalette.AlternateBase, QColor(p.surface2))
    pal.setColor(QPalette.Text, QColor(p.text))
    pal.setColor(QPalette.WindowText, QColor(p.text))
    pal.setColor(QPalette.Button, QColor(p.surface2))
    pal.setColor(QPalette.ButtonText, QColor(p.text))
    pal.setColor(QPalette.Highlight, QColor(p.accent))
    pal.setColor(QPalette.HighlightedText, QColor(p.accent_text))
    pal.setColor(QPalette.ToolTipBase, QColor(p.surface2))
    pal.setColor(QPalette.ToolTipText, QColor(p.text))
    pal.setColor(QPalette.PlaceholderText, QColor(p.muted))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(p))
    return p
