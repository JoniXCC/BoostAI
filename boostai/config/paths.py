"""Filesystem locations used by BoostAI.

All runtime state (database, logs, settings, elevation requests) lives in a single
per-user data directory, by default ``%LOCALAPPDATA%\\BoostAI``. It can be overridden
with the ``BOOSTAI_DATA_DIR`` environment variable (used by tests and portable runs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("BOOSTAI_DATA_DIR")
    if override:
        base = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "BoostAI"
    base.mkdir(parents=True, exist_ok=True)
    return base


def database_path() -> Path:
    return data_dir() / "boostai.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def elevation_dir() -> Path:
    path = data_dir() / "elevation"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_root() -> Path:
    """Root of the source tree (or the PyInstaller bundle directory when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def assets_dir() -> Path:
    return project_root() / "assets"
