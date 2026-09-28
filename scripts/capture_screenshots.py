"""Render every BoostAI page to PNG (docs/screenshots by default) for the README.

    python scripts/capture_screenshots.py [output_dir]

The window is placed off-screen and rendered with widget.grab(); it uses a temporary data
folder, runs one read-only scan and changes nothing. Review the images before publishing:
they show process names from your PC.
"""
import faulthandler
import os
import sys
import tempfile
import time

faulthandler.dump_traceback_later(240, exit=True)

os.environ["BOOSTAI_DATA_DIR"] = tempfile.mkdtemp(prefix="boostai-ui-")
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "docs", "screenshots")
os.makedirs(out, exist_ok=True)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QEventLoop
from boostai.config.logging_config import configure_logging
configure_logging()
app = QApplication(sys.argv)
from boostai.core.engine import BoostEngine
from boostai.ui import theme
from boostai.ui.main_window import MainWindow
engine = BoostEngine()
theme.apply_theme(app, os.environ.get("THEME", "dark"))
w = MainWindow(engine)
w.resize(1400, 900)
w.move(-5000, 0)
w.show()
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.02)
pump(8)
res = engine.run_scan("quick")
w.bridge.scan_finished.emit(res)
pump(2)
for key in ["dashboard", "scan", "issues", "optimizer", "processes", "monitor", "startup", "cleanup", "gaming", "history", "settings"]:
    w.show_page(key)
    pump(4 if key in ("startup", "cleanup", "processes") else 1.5)
    w.grab().save(os.path.join(out, f"{key}.png"))
    print("saved", key)
w._really_quit = True
w.close()
engine.shutdown()
print("done")
