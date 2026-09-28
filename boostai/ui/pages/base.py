from __future__ import annotations

from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from boostai.ui.bridge import EngineBridge


class Page(QScrollArea):
    """Scrollable page with standard margins. Subclasses fill ``self.body``."""

    key = "page"
    title = "Page"

    def __init__(self, bridge: EngineBridge) -> None:
        super().__init__()
        self.bridge = bridge
        self.engine = bridge.engine
        self.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("Page")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(28, 22, 28, 22)
        self.body.setSpacing(14)
        self.setWidget(inner)

    def on_show(self) -> None:
        """Called when the page becomes visible."""
