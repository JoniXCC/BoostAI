"""GUI bootstrap: QApplication, single-instance guard, theme, engine lifetime."""

from __future__ import annotations

import getpass
import logging
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from boostai import APP_NAME
from boostai.ui import theme
from boostai.ui.widgets import app_icon

log = logging.getLogger(__name__)


def _instance_name() -> str:
    return f"BoostAI-{getpass.getuser()}"


def _already_running() -> bool:
    """If another BoostAI is running for this user, ask it to show its window."""
    sock = QLocalSocket()
    sock.connectToServer(_instance_name())
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def run_gui(minimized: bool = False) -> int:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    if _already_running():
        return 0

    from boostai.core.engine import BoostEngine
    from boostai.ui.main_window import MainWindow

    try:
        engine = BoostEngine()
    except Exception as exc:
        log.exception("Engine start-up failed")
        QMessageBox.critical(None, APP_NAME, f"BoostAI could not start:\n{exc}")
        return 1
    theme.apply_theme(app, engine.settings.theme.value)

    server = QLocalServer()
    QLocalServer.removeServer(_instance_name())
    server.listen(_instance_name())
    window = MainWindow(engine)

    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(window.restore_window)

    server.newConnection.connect(on_connection)
    if minimized:
        engine.monitor.set_background(True)
    else:
        window.show()
    try:
        return app.exec()
    finally:
        engine.shutdown()
