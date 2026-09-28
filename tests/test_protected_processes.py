import pytest

from boostai.security.protected_processes import evaluate_process
from tests.fakes import USER, proc


def verdict(p, **kw):
    return evaluate_process(p, self_pid=kw.get("self_pid", 1), service_pids=kw.get("service_pids", set()), user=USER)


def test_normal_user_app_is_allowed():
    assert not verdict(proc(5000, "discord.exe")).protected


@pytest.mark.parametrize("name", ["lsass.exe", "csrss.exe", "winlogon.exe", "svchost.exe", "dwm.exe",
                                  "explorer.exe", "MsMpEng.exe", "services.exe", "System"])
def test_critical_windows_processes_are_blocked(name):
    v = verdict(proc(5000, name))
    assert v.protected
    assert "Action blocked for safety" in v.message


def test_security_software_blocked_even_if_user_owned():
    assert verdict(proc(5000, "avp.exe")).protected
    assert verdict(proc(5000, "EasyAntiCheat_launcher.exe")).protected


def test_unknown_owner_is_blocked():
    v = verdict(proc(5000, "mystery.exe", user=None))
    assert v.protected and "owner could not be verified" in v.reasons


def test_other_account_and_system_are_blocked():
    assert verdict(proc(5000, "tool.exe", user="NT AUTHORITY\\SYSTEM")).protected
    assert verdict(proc(5000, "tool.exe", user="TESTPC\\bob")).protected


def test_windows_directory_executables_blocked(monkeypatch):
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    assert verdict(proc(5000, "notepad.exe", exe="C:\\Windows\\System32\\notepad.exe")).protected


def test_self_services_session0_and_low_pids_blocked():
    assert verdict(proc(4242, "python.exe"), self_pid=4242).protected
    assert verdict(proc(5000, "host.exe"), service_pids={5000}).protected
    assert verdict(proc(5000, "host.exe", session=0)).protected
    assert verdict(proc(4, "anything.exe")).protected


def test_unknown_exe_path_blocked():
    p = proc(5000, "x.exe")
    p.exe = None
    assert verdict(p).protected
