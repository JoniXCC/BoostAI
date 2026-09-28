"""Render the programmatic app icon to assets/boostai.ico and assets/boostai.png (used by the exe/installer)."""

from pathlib import Path

from PySide6.QtGui import QGuiApplication

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    app = QGuiApplication([])  # noqa: F841 - required for QPixmap
    from boostai.ui.widgets import app_icon

    icon = app_icon()
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    icon.pixmap(256, 256).save(str(out / "boostai.png"))
    if not icon.pixmap(256, 256).save(str(out / "boostai.ico")):
        raise SystemExit("ICO writer unavailable in this Qt build")
    print("wrote", out / "boostai.ico")


if __name__ == "__main__":
    main()
