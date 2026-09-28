# PyInstaller spec for BoostAI (one-folder, windowed, runs as the invoking user - "asInvoker").
# Build with:  scripts\build.ps1   (or: pyinstaller packaging\boostai.spec --noconfirm)
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets"), "assets")],
    hiddenimports=["win32timezone", "pyqtgraph.graphicsItems.ViewBox.axisCtrlTemplate_pyside6",
                   "pyqtgraph.graphicsItems.PlotItem.plotConfigTemplate_pyside6",
                   "pyqtgraph.imageview.ImageViewTemplate_pyside6"],
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf",
              "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BoostAI",
    icon=str(ROOT / "assets" / "boostai.ico"),
    console=False,
    uac_admin=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="BoostAI")
