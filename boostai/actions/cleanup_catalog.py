"""The *complete* list of locations BoostAI's cleaner may touch, plus a dry-run scanner.

Rules enforced for every file:
* the root must pass :func:`validate_cleanup_root` (no junctions, no personal folders, ...);
* traversal never follows symlinks/junctions, and every candidate is re-checked to lie
  inside its root after path resolution;
* only files older than the configured age are candidates (default 24 h), so files in
  use by running installers/apps are skipped;
* only the named sub-folders are cleaned for browser caches - never cookies, history,
  passwords, sessions or settings;
* files that are locked are silently skipped (never forced).
"""

from __future__ import annotations

import fnmatch
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from boostai.security.safety_rules import is_reparse_point, is_within, validate_cleanup_root

MAX_FILES_PER_TARGET = 200_000
SCAN_TIME_BUDGET_S = 20.0


@dataclass(frozen=True, slots=True)
class CleanupTarget:
    target_id: str
    label: str
    description: str
    roots: tuple[str, ...]                   # may contain env vars and a single '*' profile wildcard
    file_patterns: tuple[str, ...] = ("*",)
    recursive: bool = True
    requires_admin: bool = False
    requires_closed: tuple[str, ...] = ()     # process names that must not be running
    default_selected: bool = False
    note: str = ""


_BROWSER_CACHE_DIRS = ("Cache\\Cache_Data", "Code Cache", "GPUCache")

CATALOG: dict[str, CleanupTarget] = {
    t.target_id: t
    for t in (
        CleanupTarget(
            "user_temp", "Your temporary files", "Files in your user TEMP folder older than the age limit.",
            ("%TEMP%",), default_selected=True,
        ),
        CleanupTarget(
            "windows_temp", "Windows temporary files", "Files in the Windows TEMP folder older than the age limit.",
            (r"%SystemRoot%\Temp",), requires_admin=True, default_selected=True,
        ),
        CleanupTarget(
            "crash_dumps", "Application crash dumps", "Memory dumps written when apps crashed.",
            (r"%LOCALAPPDATA%\CrashDumps",), file_patterns=("*.dmp",), default_selected=True,
        ),
        CleanupTarget(
            "wer_reports", "Windows error reports (yours)", "Archived/queued Windows Error Reporting files.",
            (r"%LOCALAPPDATA%\Microsoft\Windows\WER\ReportArchive", r"%LOCALAPPDATA%\Microsoft\Windows\WER\ReportQueue"),
            default_selected=True,
        ),
        CleanupTarget(
            "directx_shader_cache", "DirectX shader cache", "Compiled shader cache used by games.",
            (r"%LOCALAPPDATA%\D3DSCache",),
            note="Games will rebuild shaders on next launch, which can cause brief stutter.",
        ),
        CleanupTarget(
            "nvidia_shader_cache", "NVIDIA shader cache", "NVIDIA driver shader caches (DX/GL).",
            (r"%LOCALAPPDATA%\NVIDIA\DXCache", r"%LOCALAPPDATA%\NVIDIA\GLCache"),
            note="Games will rebuild shaders on next launch, which can cause brief stutter.",
        ),
        CleanupTarget(
            "thumbnail_cache", "Thumbnail cache", "Explorer picture/video thumbnails (rebuilt automatically).",
            (r"%LOCALAPPDATA%\Microsoft\Windows\Explorer",), file_patterns=("thumbcache_*.db", "iconcache_*.db"),
            recursive=False, note="Files in use by Explorer are skipped.",
        ),
        CleanupTarget(
            "chrome_cache", "Google Chrome cache (cache only)",
            "Chrome HTTP/code/GPU cache. Cookies, history, passwords and sessions are NOT touched.",
            tuple(rf"%LOCALAPPDATA%\Google\Chrome\User Data\*\{d}" for d in _BROWSER_CACHE_DIRS),
            requires_closed=("chrome.exe",), note="Chrome must be closed.",
        ),
        CleanupTarget(
            "edge_cache", "Microsoft Edge cache (cache only)",
            "Edge HTTP/code/GPU cache. Cookies, history, passwords and sessions are NOT touched.",
            tuple(rf"%LOCALAPPDATA%\Microsoft\Edge\User Data\*\{d}" for d in _BROWSER_CACHE_DIRS),
            requires_closed=("msedge.exe",), note="Edge must be closed.",
        ),
    )
}

TEMP_TARGETS = ("user_temp", "windows_temp", "crash_dumps", "wer_reports")


def _expand(pattern: str) -> str:
    if pattern.startswith("%TEMP%"):
        return tempfile.gettempdir() + pattern[len("%TEMP%"):]
    return os.path.expandvars(pattern)


def resolve_roots(target: CleanupTarget) -> list[str]:
    roots: list[str] = []
    for raw in target.roots:
        expanded = _expand(raw)
        if "%" in expanded:
            continue  # unresolved variable: never guess
        if "*" in expanded:
            head, _, tail = expanded.partition("*")
            parent = Path(head)
            if parent.is_dir():
                for profile in parent.iterdir():
                    candidate = Path(str(profile) + tail)
                    if profile.is_dir() and candidate.is_dir():
                        roots.append(str(candidate))
        elif os.path.isdir(expanded):
            roots.append(os.path.realpath(expanded))
    return roots


@dataclass(slots=True)
class TargetEstimate:
    target_id: str
    label: str
    files: int = 0
    bytes: int = 0
    roots: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    truncated: bool = False


def iter_candidates(target: CleanupTarget, min_age_hours: float, now: float | None = None):
    """Yield (path, size) for every deletable file of ``target``. Never follows links."""
    now = now or time.time()
    cutoff = now - min_age_hours * 3600
    started = time.monotonic()
    count = 0
    for root in resolve_roots(target):
        ok, _ = validate_cleanup_root(root)
        if not ok:
            continue
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except OSError:
                continue
            for entry in entries:
                if is_reparse_point(entry):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if target.recursive:
                            stack.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if not any(fnmatch.fnmatch(entry.name.lower(), p.lower()) for p in target.file_patterns):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if max(st.st_mtime, st.st_ctime) > cutoff:
                    continue
                if not is_within(entry.path, root):
                    continue
                yield entry.path, int(st.st_size)
                count += 1
                if count >= MAX_FILES_PER_TARGET or time.monotonic() - started > SCAN_TIME_BUDGET_S:
                    return


def estimate(
    target_ids: list[str] | None, min_age_hours: float, running_names: set[str] | None = None, is_admin: bool = False
) -> list[TargetEstimate]:
    """Dry run: how much each target would free. Nothing is deleted."""
    running = {n.lower() for n in (running_names or set())}
    results: list[TargetEstimate] = []
    for tid in target_ids or list(CATALOG):
        target = CATALOG.get(tid)
        if target is None:
            continue
        est = TargetEstimate(tid, target.label, roots=resolve_roots(target))
        blocking = [p for p in target.requires_closed if p.lower() in running]
        if blocking:
            est.skipped_reason = f"{', '.join(blocking)} is running - close it first"
        if not est.roots:
            est.skipped_reason = est.skipped_reason or "location not present"
        for _path, size in iter_candidates(target, min_age_hours):
            est.files += 1
            est.bytes += size
            if est.files >= MAX_FILES_PER_TARGET:
                est.truncated = True
                break
        if target.requires_admin and not is_admin and est.files == 0 and not est.skipped_reason:
            est.skipped_reason = "needs administrator permission to inspect and clean"
        results.append(est)
    return results
