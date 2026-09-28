"""Baseline comparisons: 'unusual for *this* PC' rather than 'bad in general'."""

from __future__ import annotations

from boostai.core.issues import Confidence, Issue, IssueType, RootCause, Severity
from boostai.core.models import MB, fmt_bytes, group_processes
from boostai.detection.base import DetectionContext, restart_or_close_proposals, root_instance

MIN_MEMORY = 300 * MB
MIN_DELTA = 200 * MB


def detect(ctx: DetectionContext, already_flagged: set[str]) -> list[Issue]:
    issues: list[Issue] = []
    groups = group_processes(ctx.snapshot.processes)
    for g in groups:
        lname = g.name.lower()
        if lname in already_flagged or g.private < MIN_MEMORY:
            continue
        b = ctx.baselines.process(lname, "memory")
        if b is None or not b.reliable:
            continue
        limit = max(b.p95 * 1.5, b.median * 2)
        if g.private <= limit or g.private - b.median < MIN_DELTA:
            continue
        instances = ctx.processes_named(lname)
        root = root_instance(instances)
        ratio = g.private / b.median if b.median else 0
        issues.append(Issue(
            key=f"{IssueType.PROCESS_ABOVE_BASELINE.value}:{lname}",
            type=IssueType.PROCESS_ABOVE_BASELINE,
            title=f"{g.name} is using more memory than usual",
            severity=Severity.MEDIUM if ratio >= 3 else Severity.LOW,
            confidence=Confidence.MEDIUM if b.samples >= 200 else Confidence.LOW,
            root_cause=RootCause.UNKNOWN,
            component=f"{g.name} ({g.count} process{'es' if g.count > 1 else ''})",
            evidence=[
                f"Usually on this PC: {fmt_bytes(b.median)} (95th percentile {fmt_bytes(b.p95)}, {b.samples} samples).",
                f"Now: {fmt_bytes(g.private)} ({ratio:.1f}x its usual amount).",
            ],
            metrics={"process": g.name, "current_bytes": g.private, "baseline_median": b.median, "baseline_p95": b.p95},
            explanation=(f"{g.name} is currently using significantly more memory than its recent baseline. "
                         "That can mean more open content than usual or accumulated memory; it does not by itself "
                         "mean the application is faulty."),
            proposals=[] if (g.is_foreground or ctx.is_game(root)) else restart_or_close_proposals(root, ctx, close=False),
            expected_effect=f"Restarting would likely return it to around {fmt_bytes(b.median)}.",
        ))

    count = ctx.snapshot.process_count
    base = ctx.baselines.system("process_count")
    limit = ctx.settings.process_count_warn
    if base and base.reliable:
        limit = min(limit, max(base.p95 * 1.3, base.median + 60))
    if count >= limit:
        top = sorted(groups, key=lambda x: x.count, reverse=True)[:5]
        evidence = [f"{count} processes are running."]
        if base and base.reliable:
            evidence.append(f"Usual on this PC: about {base.median:.0f} (95th percentile {base.p95:.0f}).")
        evidence.append("Most instances:\n" + "\n".join(f"{g.name:<28} x{g.count}" for g in top))
        issues.append(Issue(
            key=IssueType.EXCESSIVE_PROCESSES.value, type=IssueType.EXCESSIVE_PROCESSES,
            title=f"Unusually many processes ({count})", severity=Severity.LOW, confidence=Confidence.MEDIUM,
            root_cause=RootCause.WORKLOAD, component="Processes", evidence=evidence,
            metrics={"process_count": count},
            explanation=("Each process uses some memory and scheduler time. Browsers and chat apps create many "
                         "helper processes; closing unused apps reduces the count."),
            manual_steps=["Close applications you are not using."],
        ))
    return issues
