"""The only module that performs state-changing OS operations.

Every method is a fixed, typed operation (no command strings, no shell). Handlers
receive a :class:`SystemOps` instance, so tests inject a fake implementation and
never touch real processes, services or settings.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Protocol

import psutil

from boostai.core.models import PowerPlan, ProcessInfo, ServiceInfo, StartupItem, StartupSource
from boostai.metrics import power, services, startup
from boostai.metrics.processes import ProcessCollector
from boostai.utils import winapi

log = logging.getLogger(__name__)

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


class SystemOps(Protocol):
    # processes
    def list_processes(self) -> list[ProcessInfo]: ...
    def service_pids(self) -> set[int]: ...
    def process_cmdline(self, pid: int) -> list[str] | None: ...
    def post_close(self, pid: int) -> int: ...
    def terminate(self, pid: int) -> bool: ...
    def is_running(self, pid: int, create_time: float) -> bool: ...
    def launch(self, argv: list[str], cwd: str | None) -> bool: ...
    def trim_working_set(self, pid: int) -> bool: ...
    # startup
    def list_startup_items(self) -> list[StartupItem]: ...
    def read_startup_state(self, source: StartupSource, value_name: str) -> bytes | None: ...
    def write_startup_state(self, source: StartupSource, value_name: str, data: bytes | None) -> None: ...
    # power / network
    def list_power_plans(self) -> list[PowerPlan]: ...
    def set_power_plan(self, guid: str) -> bool: ...
    def flush_dns(self) -> bool: ...
    # services
    def get_service(self, name: str) -> ServiceInfo | None: ...
    def stop_service(self, name: str, timeout: float = 30.0) -> bool: ...
    def start_service(self, name: str, timeout: float = 30.0) -> bool: ...
    # files / system
    def memory_available(self) -> int: ...
    def delete_file(self, path: str) -> bool: ...
    def remove_empty_dir(self, path: str) -> bool: ...
    def disk_free(self, path: str) -> int: ...
    def create_restore_point(self, description: str) -> bool: ...
    def latest_restore_point(self) -> tuple[int, str] | None: ...


class WindowsSystemOps:
    def __init__(self, collector: ProcessCollector | None = None) -> None:
        self._collector = collector or ProcessCollector()
        self._service_pids: tuple[float, set[int]] | None = None

    # ------------------------------------------------------------- processes
    def list_processes(self) -> list[ProcessInfo]:
        return self._collector.collect()

    def service_pids(self) -> set[int]:
        # Service enumeration takes ~0.4 s; cache briefly so validating several actions stays fast.
        now = time.monotonic()
        if self._service_pids is None or now - self._service_pids[0] > 10.0:
            self._service_pids = (now, {s.pid for s in services.collect_services() if s.pid})
        return set(self._service_pids[1])

    def process_cmdline(self, pid: int) -> list[str] | None:
        try:
            return psutil.Process(pid).cmdline() or None
        except (psutil.Error, OSError):
            return None

    def post_close(self, pid: int) -> int:
        return winapi.post_close_to_process_windows(pid)

    def terminate(self, pid: int) -> bool:
        try:
            psutil.Process(pid).terminate()
            return True
        except psutil.NoSuchProcess:
            return True
        except (psutil.Error, OSError):
            return False

    def is_running(self, pid: int, create_time: float) -> bool:
        try:
            p = psutil.Process(pid)
            return abs(p.create_time() - create_time) < 1.0 and p.is_running()
        except (psutil.Error, OSError):
            return False

    def launch(self, argv: list[str], cwd: str | None) -> bool:
        if not argv or not os.path.isfile(argv[0]):
            return False
        try:
            subprocess.Popen(  # noqa: S603 - argv is the application's own recorded command line, never a shell string
                argv, cwd=cwd, shell=False, close_fds=True,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except OSError as exc:
            log.warning("Relaunch failed: %s", exc)
            return False

    def trim_working_set(self, pid: int) -> bool:
        return winapi.trim_working_set(pid)

    # --------------------------------------------------------------- startup
    def list_startup_items(self) -> list[StartupItem]:
        return startup.collect_startup_items()

    def read_startup_state(self, source: StartupSource, value_name: str) -> bytes | None:
        return startup.read_approved(source, value_name)

    def write_startup_state(self, source: StartupSource, value_name: str, data: bytes | None) -> None:
        startup.write_approved(source, value_name, data)

    # --------------------------------------------------------- power/network
    def list_power_plans(self) -> list[PowerPlan]:
        return power.list_power_plans()

    def set_power_plan(self, guid: str) -> bool:
        return power.set_active_scheme(guid)

    def flush_dns(self) -> bool:
        return winapi.flush_dns_cache()

    # -------------------------------------------------------------- services
    def get_service(self, name: str) -> ServiceInfo | None:
        return services.get_service(name)

    def _wait_status(self, name: str, wanted: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            svc = services.get_service(name)
            if svc and svc.status == wanted:
                return True
            time.sleep(0.5)
        return False

    def stop_service(self, name: str, timeout: float = 30.0) -> bool:
        import win32serviceutil

        try:
            win32serviceutil.StopService(name)
        except Exception as exc:  # pywintypes.error, e.g. dependent services or access denied
            log.warning("StopService(%s) failed: %s", name, exc)
            return False
        return self._wait_status(name, "stopped", timeout)

    def start_service(self, name: str, timeout: float = 30.0) -> bool:
        import win32serviceutil

        try:
            win32serviceutil.StartService(name)
        except Exception as exc:
            log.warning("StartService(%s) failed: %s", name, exc)
            return False
        return self._wait_status(name, "running", timeout)

    # --------------------------------------------------------- files/system
    def memory_available(self) -> int:
        return int(psutil.virtual_memory().available)

    def delete_file(self, path: str) -> bool:
        try:
            os.remove(path)
            return True
        except OSError:
            return False  # locked / in use / permission: skipped, never forced

    def remove_empty_dir(self, path: str) -> bool:
        try:
            os.rmdir(path)  # only succeeds if empty
            return True
        except OSError:
            return False

    def disk_free(self, path: str) -> int:
        try:
            return int(psutil.disk_usage(path).free)
        except OSError:
            return 0

    def create_restore_point(self, description: str) -> bool:
        from boostai.utils.wmi import wmi_call

        # RestorePointType 12 = MODIFY_SETTINGS, EventType 100 = BEGIN_SYSTEM_CHANGE.
        try:
            return int(wmi_call(r"root\default", "SystemRestore", "CreateRestorePoint", description, 12, 100)) == 0
        except Exception as exc:
            log.warning("Restore point creation failed: %s", exc)
            return False

    def latest_restore_point(self) -> tuple[int, str] | None:
        from boostai.utils.wmi import wmi_query

        try:
            rows = wmi_query(r"root\default", "SELECT Description, SequenceNumber FROM SystemRestore",
                             ["Description", "SequenceNumber"])
        except Exception:
            return None
        if not rows:
            return None
        newest = max(rows, key=lambda r: int(r["SequenceNumber"] or 0))
        return int(newest["SequenceNumber"] or 0), str(newest["Description"])
