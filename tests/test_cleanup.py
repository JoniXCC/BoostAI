import os
import time
from pathlib import Path

import pytest

from boostai.actions import cleanup_catalog
from boostai.actions.action_models import ActionRequest, Approval, ResultStatus
from boostai.actions.cleanup_catalog import CleanupTarget, estimate, iter_candidates
from boostai.security.safety_rules import validate_cleanup_root
from tests.fakes import proc


def make_file(path: Path, size: int, age_hours: float):
    """Create a file whose modified AND creation times are ``age_hours`` old (the cleaner checks both)."""
    import datetime

    import pywintypes
    import win32file

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    t = time.time() - age_hours * 3600
    os.utime(path, (t, t))
    stamp = pywintypes.Time(datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc))
    handle = win32file.CreateFile(str(path), win32file.GENERIC_WRITE, 0, None, win32file.OPEN_EXISTING, 0, None)
    try:
        win32file.SetFileTime(handle, stamp, stamp, stamp)
    finally:
        handle.Close()


def test_recently_created_file_with_old_mtime_is_kept(temp_target):
    """An extracted installer file can carry an old modified-date while being brand new: never a candidate."""
    f = temp_target / "fresh_extract.dll"
    f.write_bytes(b"x")
    old = time.time() - 100 * 3600
    os.utime(f, (old, old))
    assert list(iter_candidates(cleanup_catalog.CATALOG["user_temp"], 24)) == []


@pytest.fixture
def temp_target(tmp_path, monkeypatch):
    root = tmp_path / "cache_root"
    root.mkdir()
    target = CleanupTarget("user_temp", "Test temp", "d", (str(root),))
    monkeypatch.setitem(cleanup_catalog.CATALOG, "user_temp", target)
    return root


def test_only_old_files_are_candidates(temp_target):
    make_file(temp_target / "old.tmp", 100, 48)
    make_file(temp_target / "sub" / "old2.tmp", 50, 30)
    make_file(temp_target / "new.tmp", 100, 1)
    found = {Path(p).name for p, _ in iter_candidates(cleanup_catalog.CATALOG["user_temp"], 24)}
    assert found == {"old.tmp", "old2.tmp"}


def test_symlinks_are_not_followed(temp_target, tmp_path):
    outside = tmp_path / "precious"
    make_file(outside / "important.docx", 10, 100)
    try:
        os.symlink(outside, temp_target / "link", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted on this machine")
    found = [p for p, _ in iter_candidates(cleanup_catalog.CATALOG["user_temp"], 1)]
    assert not any("important.docx" in p for p in found)


def test_junctions_are_not_followed(temp_target, tmp_path):
    import _winapi

    outside = tmp_path / "precious2"
    make_file(outside / "thesis.docx", 10, 100)
    _winapi.CreateJunction(str(outside), str(temp_target / "junction"))
    found = [p for p, _ in iter_candidates(cleanup_catalog.CATALOG["user_temp"], 1)]
    assert not any("thesis.docx" in p for p in found)
    assert not validate_cleanup_root(str(temp_target / "junction"))[0]


def test_forbidden_roots_rejected(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    assert not validate_cleanup_root(str(home / "Documents"))[0]
    assert not validate_cleanup_root(str(home))[0]
    assert not validate_cleanup_root("C:\\")[0]
    assert not validate_cleanup_root(str(tmp_path / "missing"))[0]
    (home / "Documents" / "sub").mkdir()
    assert not validate_cleanup_root(str(home / "Documents" / "sub"))[0]


def test_cleanup_action_deletes_and_verifies(temp_target, executor, ops):
    make_file(temp_target / "a.tmp", 2 * 1024 * 1024, 48)
    make_file(temp_target / "keep.tmp", 10, 1)
    r = ActionRequest(action_id="CLEAR_SAFE_TEMP_FILES", params={"target_ids": ["user_temp"]})
    out = executor.execute(r, Approval(r.request_id))
    assert out.status == ResultStatus.SUCCESS
    assert not (temp_target / "a.tmp").exists() and (temp_target / "keep.tmp").exists()


def test_cleanup_rejects_unknown_or_wrong_category_targets(executor):
    r = ActionRequest(action_id="CLEAR_SAFE_TEMP_FILES", params={"target_ids": ["C:\\Users"]})
    assert executor.execute(r, Approval(r.request_id)).status == ResultStatus.BLOCKED
    r2 = ActionRequest(action_id="CLEAR_SAFE_TEMP_FILES", params={"target_ids": ["chrome_cache"]})
    assert executor.execute(r2, Approval(r2.request_id)).status == ResultStatus.BLOCKED
    r3 = ActionRequest(action_id="CLEAR_SPECIFIC_SAFE_CACHE", params={"target_ids": ["user_temp"]})
    assert executor.execute(r3, Approval(r3.request_id)).status == ResultStatus.BLOCKED


def test_browser_cache_requires_browser_closed(executor, ops):
    ops.processes = [proc(10, "chrome.exe")]
    r = ActionRequest(action_id="CLEAR_SPECIFIC_SAFE_CACHE", params={"target_ids": ["chrome_cache"]})
    out = executor.execute(r, Approval(r.request_id))
    assert out.status == ResultStatus.BLOCKED and "close chrome.exe" in out.message


def test_browser_targets_never_include_profile_data():
    for tid in ("chrome_cache", "edge_cache"):
        for root in cleanup_catalog.CATALOG[tid].roots:
            lower = root.lower()
            assert not any(x in lower for x in ("cookies", "history", "login data", "sessions", "preferences"))


def test_estimate_reports_running_browser(temp_target):
    est = estimate(["chrome_cache"], 24, running_names={"chrome.exe"})
    assert est[0].skipped_reason
