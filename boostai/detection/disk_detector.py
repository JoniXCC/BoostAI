"""Disk capacity, disk load/latency, storage health and temp-file opportunities."""

from __future__ import annotations

from boostai.actions.action_models import ActionID
from boostai.actions.cleanup_catalog import CATALOG, TEMP_TARGETS
from boostai.core.issues import ActionProposal, Confidence, Issue, IssueType, RootCause, Severity
from boostai.core.models import GB, MB, fmt_bytes
from boostai.core.trend import mean
from boostai.detection.base import DetectionContext


def _cleanup_proposal(ctx: DetectionContext) -> tuple[ActionProposal | None, int]:
    if not ctx.cleanup:
        return None, 0
    usable = [e for e in ctx.cleanup if e.target_id in TEMP_TARGETS and e.files and not e.skipped_reason]
    total = sum(e.bytes for e in usable)
    if not usable or total < 50 * MB:
        return None, total
    breakdown = ", ".join(f"{CATALOG[e.target_id].label}: {fmt_bytes(e.bytes)}" for e in usable)
    return ActionProposal(
        action_id=ActionID.CLEAR_SAFE_TEMP_FILES.value,
        params={"target_ids": [e.target_id for e in usable]},
        label=f"Remove {fmt_bytes(total)} of safe temporary files",
        rationale=f"Only approved temporary locations older than the age limit ({breakdown}).",
        warning="Deleted temporary files cannot be restored.",
    ), total


def detect(ctx: DetectionContext) -> list[Issue]:
    issues: list[Issue] = []
    s = ctx.settings
    proposal, cleanable = _cleanup_proposal(ctx)

    for vol in ctx.snapshot.disks:
        free_pct = 100 - vol.percent
        if free_pct >= s.disk_free_warn_percent and vol.free >= s.disk_free_warn_gb * GB:
            continue
        critical = free_pct < 5 or vol.free < 5 * GB
        severity = Severity.CRITICAL if critical and vol.is_system else Severity.HIGH if critical else Severity.MEDIUM
        if not vol.is_system and not critical:
            severity = Severity.LOW
        issues.append(Issue(
            key=f"{IssueType.LOW_DISK_SPACE.value}:{vol.mountpoint}",
            type=IssueType.LOW_DISK_SPACE,
            title=f"Low disk space on {vol.mountpoint} ({fmt_bytes(vol.free)} free)",
            severity=severity, confidence=Confidence.HIGH, root_cause=RootCause.CONFIGURATION,
            component=f"Drive {vol.mountpoint}",
            evidence=[f"{fmt_bytes(vol.free)} free of {fmt_bytes(vol.total)} ({free_pct:.0f}% free)."]
                     + ([f"Safe temporary files that could be removed: {fmt_bytes(cleanable)}."] if cleanable else []),
            metrics={"free_bytes": vol.free, "free_percent": free_pct},
            explanation=("Windows needs free space for updates, the page file and temporary files. Very low free "
                         "space slows the system and can make updates fail." if vol.is_system else
                         "This drive is nearly full."),
            proposals=[proposal] if proposal and vol.is_system else [],
            manual_steps=[
                "Turn on Storage Sense (Settings > System > Storage).",
                "Uninstall applications and games you no longer use (BoostAI never uninstalls software automatically).",
                "Move large personal files (videos, installers in Downloads) to another drive or cloud storage.",
            ],
            expected_effect=f"Removing temporary files would free about {fmt_bytes(cleanable)}." if cleanable else "",
        ))

    if proposal and cleanable >= 500 * MB and not any(i.type == IssueType.LOW_DISK_SPACE for i in issues):
        issues.append(Issue(
            key=IssueType.TEMP_FILES.value, type=IssueType.TEMP_FILES,
            title=f"{fmt_bytes(cleanable)} of removable temporary files", severity=Severity.LOW,
            confidence=Confidence.HIGH, root_cause=RootCause.CONFIGURATION, component="Temporary files",
            evidence=[f"{e.label}: {fmt_bytes(e.bytes)} in {e.files} files" for e in ctx.cleanup
                      if e.target_id in TEMP_TARGETS and e.files],
            explanation="Old temporary files waste disk space. Removing them does not make the PC faster by itself.",
            proposals=[proposal], expected_effect=f"About {fmt_bytes(cleanable)} of disk space recovered.",
            files_affected="Only files in the approved temporary folders listed in the evidence.",
        ))

    # Disk load / latency
    points = [p for p in ctx.history.system_points(since=ctx.now - 180) if p.disk_busy is not None]
    if len(points) >= 20:
        busy = mean([p.disk_busy for p in points])
        if busy >= 90:
            hdd = _system_is_hdd(ctx)
            top_io = sorted(ctx.snapshot.processes, key=lambda p: p.io_bps, reverse=True)[:5]
            issues.append(Issue(
                key=IssueType.DISK_BUSY.value, type=IssueType.DISK_BUSY,
                title=f"Disk constantly busy ({busy:.0f}% active)",
                severity=Severity.HIGH if hdd else Severity.MEDIUM, confidence=Confidence.MEDIUM,
                root_cause=RootCause.HARDWARE_LIMITATION if hdd else RootCause.WORKLOAD, component="Storage",
                evidence=[f"Disk active {busy:.0f}% of the time over the last 3 minutes.",
                          "Highest I/O processes right now:\n" + "\n".join(
                              f"{p.name:<28} {fmt_bytes(p.io_bps)}/s" for p in top_io)],
                metrics={"disk_busy_percent": busy},
                explanation=("The system drive is saturated, which makes everything that needs the disk wait." +
                             (" It is a hard disk drive (HDD), which is a hardware limitation." if hdd else "")),
                manual_steps=["Let updates or downloads finish."] +
                             (["Upgrading the system drive to an SSD is the most effective fix."] if hdd else []),
            ))

    # Storage health / media type
    for dev in ctx.system_info.storage:
        if dev.health in ("Warning", "Unhealthy"):
            issues.append(Issue(
                key=f"{IssueType.STORAGE_HEALTH.value}:{dev.name}", type=IssueType.STORAGE_HEALTH,
                title=f"Storage health: {dev.name} reports '{dev.health}'",
                severity=Severity.CRITICAL if dev.health == "Unhealthy" else Severity.HIGH,
                confidence=Confidence.HIGH, root_cause=RootCause.FAILING_STORAGE, component=dev.name,
                evidence=[f"Windows Storage Management reports health status '{dev.health}' for {dev.name}."],
                explanation="The drive itself reports a problem. This cannot be fixed by software.",
                manual_steps=["Back up important files now.", "Run the drive manufacturer's diagnostic tool.",
                              "Plan to replace the drive."],
            ))
    if _system_is_hdd(ctx):
        issues.append(Issue(
            key=IssueType.SLOW_SYSTEM_DRIVE.value, type=IssueType.SLOW_SYSTEM_DRIVE,
            title="Windows runs from a hard disk drive (HDD)", severity=Severity.INFO, confidence=Confidence.MEDIUM,
            root_cause=RootCause.HARDWARE_LIMITATION, component="Storage",
            evidence=["No SSD was detected; the only physical disk(s) report media type HDD."],
            explanation="HDDs are much slower than SSDs for the random access Windows performs constantly.",
            manual_steps=["Upgrading to an SSD is the single biggest responsiveness improvement for this PC."],
        ))
    return issues


def _system_is_hdd(ctx: DetectionContext) -> bool:
    kinds = {d.media_type for d in ctx.system_info.storage}
    return "HDD" in kinds and "SSD" not in kinds and "SCM" not in kinds
