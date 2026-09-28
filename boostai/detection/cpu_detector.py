"""CPU detectors: sustained load, runaway background processes, idle load, spikes, DPC/interrupts.

Context matters: a game using most of the CPU while you play is expected, so load that is
attributable to the foreground full-screen app or a recognised game is reported as INFO
(or not at all), never as a problem to "fix".
"""

from __future__ import annotations

from boostai.core.issues import Confidence, Issue, IssueType, RootCause, Severity
from boostai.core.models import ProcessInfo
from boostai.core.trend import mean
from boostai.detection.base import DetectionContext, restart_or_close_proposals

SUSPICIOUS_PATH_MARKERS = ("\\appdata\\local\\temp\\", "\\windows\\temp\\", "\\downloads\\", "\\$recycle.bin\\")


def _sustained_processes(ctx: DetectionContext, minutes: float) -> list[tuple[ProcessInfo, float]]:
    """(process, average CPU%) over the window, for processes observed for at least 80% of it."""
    since = ctx.now - minutes * 60
    out = []
    for p in ctx.snapshot.processes:
        values = ctx.history.cpu_window(p.key, since)
        if len(values) < 3 or ctx.history.cpu_coverage_seconds(p.key) < 0.8 * minutes * 60:
            continue
        out.append((p, mean(values)))
    return sorted(out, key=lambda x: x[1], reverse=True)


def detect(ctx: DetectionContext) -> list[Issue]:
    issues: list[Issue] = []
    s = ctx.settings
    window = s.cpu_sustained_minutes
    points = ctx.history.system_points(since=ctx.now - window * 60)
    enough = len(points) >= 5 and (points[-1].t - points[0].t) >= 0.8 * window * 60 if points else False
    sustained = _sustained_processes(ctx, window)
    logical = max(ctx.system_info.logical_cores, 1)
    one_core = 100.0 / logical

    # 1) Sustained high total CPU
    if enough:
        avg = mean([p.cpu for p in points])
        share_high = sum(1 for p in points if p.cpu >= s.cpu_high_percent - 10) / len(points)
        if avg >= s.cpu_high_percent and share_high >= 0.8:
            issues.append(_sustained_issue(ctx, avg, window, sustained))

    # 2) Background process pinning CPU (runaway) - not the foreground app, not a game
    for proc, avg in sustained[:10]:
        if proc.is_foreground or ctx.is_game(proc):
            continue
        heavy = avg >= s.process_cpu_monopoly_percent
        pinned_core = avg >= max(0.9 * one_core, 3.0)
        if not (heavy or pinned_core):
            continue
        if proc.pid in ctx.service_pids or proc.name.lower() == "system":
            continue  # services are handled by the service detector
        issues.append(_monopoly_issue(ctx, proc, avg, window, one_core))

    # 3) High CPU while the user is idle
    idle_issue = _idle_issue(ctx, sustained)
    if idle_issue:
        issues.append(idle_issue)

    # 4) Repeated spikes
    spikes = _spike_issue(ctx)
    if spikes:
        issues.append(spikes)

    # 5) Interrupt / DPC time (driver or hardware)
    irq_points = ctx.history.system_points(since=ctx.now - 120)
    if len(irq_points) >= 10:
        irq = mean([p.interrupt_dpc for p in irq_points])
        if irq >= 10:
            issues.append(Issue(
                key=IssueType.HIGH_INTERRUPT_CPU.value, type=IssueType.HIGH_INTERRUPT_CPU,
                title=f"High interrupt/DPC CPU time ({irq:.0f}%)",
                severity=Severity.MEDIUM if irq < 20 else Severity.HIGH, confidence=Confidence.MEDIUM,
                root_cause=RootCause.DRIVER_ISSUE, component="Device drivers / hardware",
                evidence=[f"Interrupt + DPC time averaged {irq:.1f}% of total CPU over the last 2 minutes "
                          "(typically well under 2%)."],
                metrics={"interrupt_dpc_percent": irq},
                explanation=("CPU time spent servicing hardware interrupts and deferred procedure calls is unusually "
                             "high. This is usually caused by a driver (network, audio, storage, GPU) or failing "
                             "hardware, and cannot be fixed safely by an automatic action."),
                manual_steps=[
                    "Update chipset, network, audio and graphics drivers from the PC or component manufacturer.",
                    "Use a latency analysis tool (e.g. LatencyMon) to identify the responsible driver.",
                    "Disconnect recently added USB devices to see if the problem stops.",
                ],
            ))

    # 6) Unusual behaviour: sustained CPU from executables in temp/download locations while idle
    idle_now = (ctx.snapshot.user_idle_seconds or 0) >= s.user_idle_seconds
    for proc, avg in sustained[:15]:
        exe = (proc.exe or "").lower()
        if avg >= 10 and idle_now and any(m in exe for m in SUSPICIOUS_PATH_MARKERS):
            issues.append(Issue(
                key=f"{IssueType.UNUSUAL_PROCESS_BEHAVIOR.value}:{proc.name.lower()}",
                type=IssueType.UNUSUAL_PROCESS_BEHAVIOR,
                title=f"Unusual process behaviour: {proc.name}",
                severity=Severity.MEDIUM, confidence=Confidence.LOW, root_cause=RootCause.MALWARE_SUSPICION,
                component=f"{proc.name} (PID {proc.pid})",
                evidence=[f"Averaged {avg:.0f}% CPU for {window:.0f}+ minutes while you were away from the PC.",
                          "It runs from a temporary or downloads folder, which is unusual for installed software."],
                metrics={"process": proc.name, "cpu_percent": avg},
                explanation=("Unusual process behavior detected. This application does not determine whether "
                             "software is malicious."),
                manual_steps=["Run a scan with Windows Security or your trusted antivirus.",
                              "If you do not recognise this program, do not run it again until it has been checked."],
            ))
    return issues


def _top_lines(sustained: list[tuple[ProcessInfo, float]], n: int = 5) -> str:
    return "\n".join(f"{p.name:<28} {avg:5.1f}%" for p, avg in sustained[:n])


def _sustained_issue(ctx: DetectionContext, avg: float, window: float, sustained) -> Issue:
    top = sustained[0][0] if sustained else None
    expected = bool(top and (ctx.is_game(top) or (top.is_foreground and ctx.fullscreen_foreground)))
    evidence = [f"Total CPU averaged {avg:.0f}% over the last {window:.0f} minutes."]
    if sustained:
        evidence.append("Top consumers (average over the window, % of total CPU):\n" + _top_lines(sustained))
    return Issue(
        key=IssueType.SUSTAINED_HIGH_CPU.value, type=IssueType.SUSTAINED_HIGH_CPU,
        title=f"Sustained high CPU usage ({avg:.0f}%)",
        severity=Severity.INFO if expected else Severity.MEDIUM if avg < 95 else Severity.HIGH,
        confidence=Confidence.HIGH, root_cause=RootCause.WORKLOAD if expected else RootCause.UNKNOWN,
        component="Processor",
        evidence=evidence,
        metrics={"cpu_percent": avg, "top": [{"name": p.name, "cpu": a} for p, a in sustained[:5]]},
        explanation=(
            f"The CPU is busy mainly because of {top.name}, which is the game/app you are using. This is expected "
            "during gaming or heavy work and is not a problem to fix." if expected else
            "The processor has been almost fully busy for several minutes. If you are not running demanding work, "
            "check the top consumers listed."),
        manual_steps=[] if expected else ["Check whether the top consumer is doing expected work (e.g. rendering, "
                                          "compiling, updating)."],
    )


def _monopoly_issue(ctx: DetectionContext, proc: ProcessInfo, avg: float, window: float, one_core: float) -> Issue:
    cores = avg / one_core if one_core else 0.0
    return Issue(
        key=f"{IssueType.CPU_MONOPOLY.value}:{proc.name.lower()}",
        type=IssueType.CPU_MONOPOLY,
        title=f"Background process using constant CPU: {proc.name}",
        severity=Severity.MEDIUM if avg >= 15 else Severity.LOW,
        confidence=Confidence.MEDIUM,
        root_cause=RootCause.SOFTWARE_ISSUE,
        component=f"{proc.name} (PID {proc.pid})",
        evidence=[f"{proc.name} averaged {avg:.1f}% of total CPU (about {cores:.1f} logical cores) for the last "
                  f"{window:.0f} minutes while not being the active window."],
        metrics={"process": proc.name, "pid": proc.pid, "cpu_percent": avg, "cores": round(cores, 2)},
        explanation=("A background application keeping one or more CPU cores busy continuously is often stuck in a "
                     "loop or doing work you do not need right now. It increases heat, fan noise and power use."),
        proposals=restart_or_close_proposals(proc, ctx),
        manual_steps=["If the program is doing expected work (sync, encoding, updating), let it finish."],
        expected_effect=f"Restarting or closing it would free roughly {avg:.0f}% of total CPU.",
    )


def _idle_issue(ctx: DetectionContext, sustained) -> Issue | None:
    s = ctx.settings
    points = [p for p in ctx.history.system_points(since=ctx.now - 15 * 60)
              if p.user_idle is not None and p.user_idle >= s.user_idle_seconds]
    if len(points) < 10 or points[-1].t - points[0].t < 120:
        return None
    avg = mean([p.cpu for p in points])
    baseline = ctx.baselines.system("idle_cpu")
    threshold = s.idle_cpu_percent
    if baseline and baseline.reliable:
        threshold = max(threshold, baseline.p95 * 1.2)
    if avg < threshold:
        return None
    evidence = [f"CPU averaged {avg:.0f}% during {len(points)} samples when there was no keyboard/mouse input."]
    if baseline and baseline.reliable:
        evidence.append(f"This PC's usual idle CPU is about {baseline.median:.0f}% (95th percentile {baseline.p95:.0f}%).")
    background = [(p, a) for p, a in sustained if not p.is_foreground][:5]
    if background:
        evidence.append("Main background consumers:\n" + _top_lines(background))
    return Issue(
        key=IssueType.HIGH_IDLE_CPU.value, type=IssueType.HIGH_IDLE_CPU,
        title=f"High CPU usage while idle ({avg:.0f}%)",
        severity=Severity.MEDIUM if avg >= 25 else Severity.LOW, confidence=Confidence.MEDIUM,
        root_cause=RootCause.SOFTWARE_ISSUE, component="Background processes", evidence=evidence,
        metrics={"idle_cpu_percent": avg},
        explanation=("Your PC is doing significant work when you are not using it. Windows updates, antivirus scans "
                     "and indexing do this temporarily; if it persists, a background application is the likely cause."),
        manual_steps=["If this persists for hours, review the background consumers listed above."],
    )


def _spike_issue(ctx: DetectionContext) -> Issue | None:
    points = ctx.history.system_points(since=ctx.now - 10 * 60)
    if len(points) < 30:
        return None
    spikes = 0
    below = True
    for p in points:
        if below and p.cpu >= 90:
            spikes += 1
            below = False
        elif p.cpu < 60:
            below = True
    if spikes < 6:
        return None
    return Issue(
        key=IssueType.CPU_SPIKES.value, type=IssueType.CPU_SPIKES,
        title=f"Repeated CPU spikes ({spikes} in 10 minutes)", severity=Severity.LOW, confidence=Confidence.MEDIUM,
        root_cause=RootCause.UNKNOWN, component="Processor",
        evidence=[f"CPU jumped above 90% {spikes} separate times in the last 10 minutes."],
        metrics={"spikes": spikes},
        explanation="Frequent short CPU spikes can cause stutter. They are often caused by scheduled background tasks.",
        manual_steps=["Open the Monitor page during a spike to see which process is responsible."],
    )
