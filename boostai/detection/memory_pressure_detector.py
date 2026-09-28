"""RAM pressure, commit-limit pressure and heavy paging.

Explains *why* memory is full - a single runaway process vs. many programs open vs. a
machine that simply has too little RAM for its workload - instead of just saying "94%".
"""

from __future__ import annotations

from boostai.core.issues import ActionProposal, Confidence, Issue, IssueType, RootCause, Severity
from boostai.core.models import GB, MB, PressureLevel, fmt_bytes, group_processes
from boostai.core.trend import mean
from boostai.detection.base import DetectionContext, restart_or_close_proposals, root_instance

MAX_CLOSE_PROPOSALS = 3


def detect(ctx: DetectionContext, leak_processes: set[str]) -> list[Issue]:
    issues: list[Issue] = []
    mem = ctx.snapshot.memory
    pressure = ctx.snapshot.memory_pressure
    high_ram = mem.percent >= ctx.settings.ram_high_percent
    if pressure in (PressureLevel.HIGH, PressureLevel.CRITICAL) or high_ram:
        issues.append(_pressure_issue(ctx, leak_processes))
    commit = mem.commit_percent
    if commit is not None and commit >= 90:
        issues.append(_commit_issue(ctx, commit))
    paging = _heavy_paging(ctx)
    if paging:
        issues.append(paging)
    return issues


def _pressure_issue(ctx: DetectionContext, leak_processes: set[str]) -> Issue:
    mem = ctx.snapshot.memory
    groups = [g for g in group_processes(ctx.snapshot.processes) if g.name.lower() not in ("memcompression", "memory compression", "system", "registry")]
    top = groups[:6]
    used = max(mem.used, 1)
    largest_share = top[0].rss / used if top else 0.0

    contributors = [f"{g.name:<28} {fmt_bytes(g.rss):>9}" + (f"  ({g.count} processes)" if g.count > 1 else "") for g in top]
    evidence = [
        f"RAM usage: {mem.percent:.0f}% ({fmt_bytes(mem.used)} of {fmt_bytes(mem.total)}); "
        f"available: {fmt_bytes(mem.available)}.",
        f"Memory pressure level: {ctx.snapshot.memory_pressure.value}.",
    ]
    if mem.commit_percent is not None:
        evidence.append(f"Commit charge: {fmt_bytes(mem.commit_total)} of {fmt_bytes(mem.commit_limit)} limit "
                        f"({mem.commit_percent:.0f}%).")
    if mem.compressed_bytes:
        evidence.append(f"Windows is compressing {fmt_bytes(mem.compressed_bytes)} of memory to fit everything in RAM.")
    evidence.append("Main contributors (physical RAM in use):\n" + "\n".join(contributors))

    leaking = [g for g in top if g.name.lower() in leak_processes]
    persistent = _persistently_high(ctx)
    if leaking:
        cause = RootCause.POSSIBLE_MEMORY_LEAK
        explanation = (f"A possible memory leak in {leaking[0].name} is a major contributor to the current memory "
                       "pressure. See the separate memory-leak issue for its evidence.")
    elif largest_share >= 0.4:
        cause = RootCause.SOFTWARE_ISSUE
        explanation = (f"No memory leak pattern was detected, but a single application ({top[0].name}) is using "
                       f"{largest_share:.0%} of the memory in use.")
    elif persistent and mem.total <= 8 * GB:
        cause = RootCause.INSUFFICIENT_RAM
        explanation = ("No single memory leak was detected. Memory has been near its limit for most of the observed "
                       f"period with {fmt_bytes(mem.total)} installed, which points to insufficient RAM for this workload.")
    else:
        cause = RootCause.WORKLOAD
        explanation = ("No single memory leak was detected. Memory is high because several programs together are "
                       "using most of the RAM. This is different from a leak: usage should drop when you close programs.")

    proposals: list[ActionProposal] = []
    names_seen: set[str] = set()
    for g in groups:
        if len(proposals) >= MAX_CLOSE_PROPOSALS or g.rss < 400 * MB:
            break
        instances = ctx.processes_named(g.name.lower())
        root = root_instance(instances)
        if g.is_foreground or ctx.is_game(root) or g.name.lower() in names_seen:
            continue
        names_seen.add(g.name.lower())
        proposals += restart_or_close_proposals(
            root, ctx, restart=False,
            reason=f"{g.name} is using {fmt_bytes(g.rss)} of RAM and is not the app you are currently using.",
        )

    manual = [
        "Close applications and browser tabs you are not using, especially before gaming or heavy work.",
    ]
    if cause == RootCause.INSUFFICIENT_RAM or mem.total <= 8 * GB:
        manual.append(f"This PC has {fmt_bytes(mem.total)} of RAM. If this happens regularly, a RAM upgrade is "
                      "the only real fix for the hardware limitation.")
    severity = {
        PressureLevel.CRITICAL: Severity.CRITICAL, PressureLevel.HIGH: Severity.HIGH,
    }.get(ctx.snapshot.memory_pressure, Severity.MEDIUM)
    return Issue(
        key=IssueType.MEMORY_PRESSURE.value,
        type=IssueType.MEMORY_PRESSURE,
        title=(f"High memory usage ({mem.percent:.0f}%)" if mem.percent >= ctx.settings.ram_high_percent
               else f"High memory pressure (commit {mem.commit_percent or 0:.0f}%, RAM {mem.percent:.0f}%)"),
        severity=severity,
        confidence=Confidence.HIGH,
        root_cause=cause,
        component="Physical memory (RAM)",
        evidence=evidence,
        metrics={"ram_percent": mem.percent, "available_bytes": mem.available, "commit_percent": mem.commit_percent,
                 "top": [{"name": g.name, "bytes": g.rss, "count": g.count} for g in top]},
        explanation=explanation,
        proposals=proposals,
        manual_steps=manual,
        expected_effect="Closing a listed application frees roughly the memory shown next to it.",
    )


def _persistently_high(ctx: DetectionContext) -> bool:
    points = ctx.history.system_points(since=ctx.now - 30 * 60)
    if len(points) < 30:
        return False
    return sum(1 for p in points if p.ram_percent >= 85) / len(points) >= 0.8


def _commit_issue(ctx: DetectionContext, commit: float) -> Issue:
    mem = ctx.snapshot.memory
    return Issue(
        key=IssueType.HIGH_COMMIT.value,
        type=IssueType.HIGH_COMMIT,
        title=f"Commit charge near its limit ({commit:.0f}%)",
        severity=Severity.HIGH if commit >= 95 else Severity.MEDIUM,
        confidence=Confidence.HIGH,
        root_cause=RootCause.CONFIGURATION if (mem.pagefile_total or 0) < mem.total else RootCause.WORKLOAD,
        component="Virtual memory (RAM + page file)",
        evidence=[
            f"Committed memory: {fmt_bytes(mem.commit_total)} of a {fmt_bytes(mem.commit_limit)} limit.",
            f"Page file size: {fmt_bytes(mem.pagefile_total)} (used {fmt_bytes(mem.pagefile_used)}).",
        ],
        metrics={"commit_percent": commit},
        explanation=("Programs have reserved almost all the virtual memory Windows can provide (RAM plus page file). "
                     "When the limit is reached, applications can fail to allocate memory and crash."),
        manual_steps=[
            "Close programs you are not using.",
            "Make sure the page file is 'System managed' (Settings > System > About > Advanced system settings > "
            "Performance > Advanced > Virtual memory). BoostAI does not change page-file settings automatically.",
        ],
        expected_effect="Closing large applications lowers the commit charge immediately.",
    )


def _heavy_paging(ctx: DetectionContext) -> Issue | None:
    points = [p for p in ctx.history.system_points(since=ctx.now - 120) if p.hard_faults is not None]
    if len(points) < 10:
        return None
    avg_faults = mean([p.hard_faults for p in points])
    avail_pct = 100 * ctx.snapshot.memory.available / max(ctx.snapshot.memory.total, 1)
    if avg_faults < 1500 or avail_pct > 15:
        return None
    return Issue(
        key=IssueType.HEAVY_PAGING.value,
        type=IssueType.HEAVY_PAGING,
        title="Heavy paging to disk",
        severity=Severity.HIGH,
        confidence=Confidence.MEDIUM,
        root_cause=RootCause.INSUFFICIENT_RAM if ctx.snapshot.memory.total <= 8 * GB else RootCause.WORKLOAD,
        component="Memory / storage",
        evidence=[f"Average of {avg_faults:.0f} pages read from disk per second over the last 2 minutes, "
                  f"with only {avail_pct:.0f}% RAM available."],
        metrics={"hard_faults_per_sec": avg_faults},
        explanation=("Windows is repeatedly reading memory pages back from disk because RAM is full. This is a major "
                     "cause of stutter and slow app switching."),
        manual_steps=["Close memory-heavy applications (see the memory usage issue for the largest ones)."],
        expected_effect="Paging drops once enough RAM is free.",
    )
