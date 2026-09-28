"""Per-action elevation.

BoostAI's UI never runs as administrator. When an approved action needs admin rights,
:class:`ElevationBroker` writes the *structured* request (action ID + parameters, never a
command) to the per-user data folder and launches BoostAI's own executable with
``--elevated-helper <file>`` through UAC. The helper:

1. only accepts request files inside the elevation folder with the expected name;
2. re-runs the full registry + schema + safety validation;
3. executes that single whitelisted action, verifies it, writes the result, and exits.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from boostai.config import paths
from boostai.config.logging_config import log_event

log = logging.getLogger(__name__)

REQUEST_NAME_RE = re.compile(r"^[0-9a-f]{32}\.req\.json$")
ERROR_CANCELLED = 1223
HELPER_TIMEOUT_S = 300


def helper_command() -> tuple[str, list[str]]:
    """Executable and base arguments to start BoostAI itself (frozen exe or python -m boostai)."""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe), ["-m", "boostai"]


class ElevationBroker:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or paths.elevation_dir()

    def run(self, payload: dict[str, Any], timeout_s: float = HELPER_TIMEOUT_S) -> dict[str, Any]:
        nonce = uuid.uuid4().hex
        req = self.directory / f"{nonce}.req.json"
        res = self.directory / f"{nonce}.res.json"
        req.write_text(json.dumps(payload), encoding="utf-8")
        log_event("elevation_requested", action_id=payload.get("action_id"), op=payload.get("op"))
        try:
            return self._launch_and_wait(req, res, timeout_s)
        finally:
            for f in (req, res):
                try:
                    f.unlink()
                except OSError:
                    pass

    def _launch_and_wait(self, req: Path, res: Path, timeout_s: float) -> dict[str, Any]:
        try:
            import pywintypes
            import win32con
            import win32event
            import win32process
            from win32com.shell import shell, shellcon
        except ImportError:
            return {"status": "FAILED", "message": "Elevation is only available on Windows."}

        exe, base_args = helper_command()
        params = " ".join([*base_args, "--elevated-helper", f'"{req}"'])
        try:
            info = shell.ShellExecuteEx(
                fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
                lpVerb="runas",
                lpFile=exe,
                lpParameters=params,
                nShow=win32con.SW_HIDE,
            )
        except pywintypes.error as exc:
            if exc.winerror == ERROR_CANCELLED:
                return {"status": "CANCELLED", "message": "Administrator permission was declined. Nothing was changed."}
            return {"status": "FAILED", "message": f"Could not start the elevated helper: {exc.strerror}"}
        handle = info["hProcess"]
        try:
            rc = win32event.WaitForSingleObject(handle, int(timeout_s * 1000))
            if rc != win32event.WAIT_OBJECT_0:
                return {"status": "FAILED", "message": "The elevated helper timed out."}
            exit_code = win32process.GetExitCodeProcess(handle)
        finally:
            handle.Close()
        if not res.exists():
            return {"status": "FAILED", "message": f"The elevated helper returned no result (exit code {exit_code})."}
        return json.loads(res.read_text(encoding="utf-8"))


def validate_request_path(path_str: str) -> Path:
    """Only accept request files BoostAI itself created in its elevation folder."""
    path = Path(path_str).resolve()
    directory = paths.elevation_dir().resolve()
    if path.parent != directory or not REQUEST_NAME_RE.match(path.name):
        raise PermissionError("Rejected elevation request outside BoostAI's elevation folder.")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path


def result_path_for(request_path: Path) -> Path:
    return request_path.with_name(request_path.name.replace(".req.json", ".res.json"))


def write_result(request_path: Path, result: dict[str, Any]) -> None:
    out = result_path_for(request_path)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, default=str), encoding="utf-8")
    os.replace(tmp, out)
