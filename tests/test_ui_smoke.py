"""GUI smoke test: every page builds and renders against a real (read-only) engine."""

import pytest

pytest.importorskip("pytestqt")


@pytest.mark.integration
def test_main_window_pages_render(qtbot, tmp_path):
    from boostai.config.settings import SettingsStore
    from boostai.core.engine import BoostEngine
    from boostai.ui import theme
    from boostai.ui.main_window import MainWindow
    from tests.fakes import FakeSystemOps

    engine = BoostEngine(settings_store=SettingsStore(tmp_path / "s.json"), db_path=tmp_path / "t.db",
                         ops=FakeSystemOps(), start_monitor=False)
    try:
        theme.apply_theme(qtbot_app(), "dark")
        window = MainWindow(engine)
        qtbot.addWidget(window)
        result = engine.run_scan("quick")
        window.bridge.scan_finished.emit(result)
        for key in window.pages:
            if key in ("cleanup", "startup", "processes"):
                continue  # these start background OS enumeration; covered by integration runs
            window.show_page(key)
            assert window.stack.currentWidget() is window.pages[key]
        assert window.pages["issues"].list.count() == len(result.issues)
        window._really_quit = True
    finally:
        engine.shutdown()


def qtbot_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance()
