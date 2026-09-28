"""Cross-cutting safety rules: filesystem boundaries and optimisation-mode policy."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from boostai.config.settings import OptimizationMode

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_SYSTEM = 0x4


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def forbidden_roots() -> list[str]:
    """Locations that cleanup may never target (and may never be *inside of* a target root)."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    roots = [
        home, home / "Documents", home / "Desktop", home / "Pictures", home / "Videos", home / "Downloads",
        home / "Music", home / "OneDrive", home / "Favorites", home / "Saved Games",
        sysroot, sysroot / "System32", sysroot / "SysWOW64", sysroot / "WinSxS",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramData", r"C:\ProgramData")),
        Path(os.environ.get("APPDATA", str(home / "AppData/Roaming"))),
        Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))),
    ]
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        roots.append(Path(onedrive))
    return [_norm(r) for r in roots]


def user_content_roots() -> list[str]:
    """Personal-content folders: nothing beneath these may ever be deleted."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    roots = [home / n for n in ("Documents", "Desktop", "Pictures", "Videos", "Downloads", "Music", "OneDrive")]
    if os.environ.get("OneDrive"):
        roots.append(Path(os.environ["OneDrive"]))
    return [_norm(r) for r in roots]


def is_within(path: str | os.PathLike, root: str | os.PathLike) -> bool:
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def validate_cleanup_root(root: str | os.PathLike) -> tuple[bool, str]:
    """A cleanup root must exist, be a real directory (no junction), not be a forbidden
    location itself, not be a drive root and not be inside personal-content folders."""
    if not root:
        return False, "empty path"
    norm = _norm(root)
    if not os.path.isdir(norm):
        return False, "does not exist"
    if os.path.splitdrive(norm)[1] in ("", "\\", "/"):
        return False, "drive roots are never cleaned"
    if norm in forbidden_roots():
        return False, "protected location"
    if any(is_within(norm, u) for u in user_content_roots()):
        return False, "inside a personal-content folder"
    try:
        st = os.lstat(root)
        if getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT or stat.S_ISLNK(st.st_mode):
            return False, "is a junction or symbolic link"
    except OSError as exc:
        return False, f"not accessible ({exc.strerror})"
    return True, "ok"


def is_reparse_point(entry: os.DirEntry) -> bool:
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True  # unknown => treat as unsafe
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT) or entry.is_symlink()


# ------------------------------------------------------------------ mode policy
def risk_allowed(risk: str, mode: OptimizationMode) -> bool:
    """SAFE mode executes LOW risk only; BALANCED executes LOW and MEDIUM; HIGH is advice-only everywhere."""
    if risk == "HIGH":
        return False
    if mode == OptimizationMode.SAFE:
        return risk == "LOW"
    return risk in ("LOW", "MEDIUM")
